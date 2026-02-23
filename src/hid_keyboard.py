#!/usr/bin/env python3
"""Spotifone Classic BT HID Keyboard Server

Registers a Classic Bluetooth HID Keyboard profile via BlueZ D-Bus API.
BlueZ ProfileManager1 handles L2CAP PSM 17 (Control) and PSM 19 (Interrupt)
listeners and delivers connected file descriptors via NewConnection callback.
Receives key events via UNIX socket IPC from button_listener.py.

Replaces the BLE HOGP approach (ble_hid_gatt.py / run_all.py) which never
reliably delivered key events to macOS due to address caching, strict HOGP
requirements, and dual-mode BLE+Classic conflicts.

Architecture:
  - D-Bus pairing agent (DisplayOnly) for macOS pairing
  - SDP registration via ProfileManager1 with inline HID record
  - ProfileManager1 manages L2CAP PSM 17+19 (no manual socket binding)
  - NewConnection callback receives connected fds from BlueZ
  - IPC socket /tmp/spotifone_hid.sock (2-byte protocol: [keycode, pressed])
  - HID reports sent on Interrupt channel fd

Usage:
  python3 hid_keyboard.py          # Normal operation
  python3 hid_keyboard.py --test   # Send test keystroke after 5s
"""

import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib
import socket
import os
import sys
import logging
import signal
import errno

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(name)s %(message)s'
)
logger = logging.getLogger('hid_keyboard')

# D-Bus constants
BLUEZ_SERVICE = 'org.bluez'
AGENT_IFACE = 'org.bluez.Agent1'
AGENT_PATH = '/org/spotifone/agent'
PROFILE_IFACE = 'org.bluez.Profile1'
PROFILE_PATH = '/org/spotifone/hid_profile'

# L2CAP constants
AF_BLUETOOTH = 31
BTPROTO_L2CAP = 0
L2CAP_PSM_CTRL = 17   # HID Control channel
L2CAP_PSM_INTR = 19   # HID Interrupt channel

# IPC
HID_SOCK_PATH = '/tmp/spotifone_hid.sock'

# HID Report Descriptor — standard USB keyboard
# Same descriptor as used in ble_hid_gatt.py
HID_REPORT_DESCRIPTOR = bytes([
    0x05, 0x01,        # Usage Page (Generic Desktop)
    0x09, 0x06,        # Usage (Keyboard)
    0xA1, 0x01,        # Collection (Application)
    0x85, 0x01,        #   Report ID (1)
    # Modifier byte (8 bits)
    0x05, 0x07,        #   Usage Page (Key Codes)
    0x19, 0xE0,        #   Usage Minimum (224 = Left Control)
    0x29, 0xE7,        #   Usage Maximum (231 = Right GUI)
    0x15, 0x00,        #   Logical Minimum (0)
    0x25, 0x01,        #   Logical Maximum (1)
    0x75, 0x01,        #   Report Size (1)
    0x95, 0x08,        #   Report Count (8)
    0x81, 0x02,        #   Input (Data, Variable, Absolute)
    # Reserved byte
    0x95, 0x01,        #   Report Count (1)
    0x75, 0x08,        #   Report Size (8)
    0x81, 0x01,        #   Input (Constant)
    # Key array (6 keys)
    0x95, 0x06,        #   Report Count (6)
    0x75, 0x08,        #   Report Size (8)
    0x15, 0x00,        #   Logical Minimum (0)
    0x25, 0x65,        #   Logical Maximum (101)
    0x05, 0x07,        #   Usage Page (Key Codes)
    0x19, 0x00,        #   Usage Minimum (0)
    0x29, 0x65,        #   Usage Maximum (101)
    0x81, 0x00,        #   Input (Data, Array)
    0xC0,              # End Collection
])

# SDP record XML for HID Keyboard profile
# Registered via ProfileManager1.RegisterProfile()
SDP_RECORD_XML = """<?xml version="1.0" encoding="UTF-8" ?>
<record>
  <attribute id="0x0001"> <!-- ServiceClassIDList -->
    <sequence>
      <uuid value="0x1124"/> <!-- HID -->
    </sequence>
  </attribute>
  <attribute id="0x0004"> <!-- ProtocolDescriptorList -->
    <sequence>
      <sequence>
        <uuid value="0x0100"/> <!-- L2CAP -->
        <uint16 value="0x0011"/> <!-- PSM 17 = HID Control -->
      </sequence>
      <sequence>
        <uuid value="0x0011"/> <!-- HIDP -->
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x0005"> <!-- BrowseGroupList -->
    <sequence>
      <uuid value="0x1002"/> <!-- PublicBrowseRoot -->
    </sequence>
  </attribute>
  <attribute id="0x0006"> <!-- LanguageBaseAttributeIDList -->
    <sequence>
      <uint16 value="0x656e"/> <!-- en -->
      <uint16 value="0x006a"/> <!-- UTF-8 -->
      <uint16 value="0x0100"/> <!-- base -->
    </sequence>
  </attribute>
  <attribute id="0x0009"> <!-- BluetoothProfileDescriptorList -->
    <sequence>
      <sequence>
        <uuid value="0x1124"/> <!-- HID -->
        <uint16 value="0x0101"/> <!-- Version 1.1 -->
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x000d"> <!-- AdditionalProtocolDescriptorLists -->
    <sequence>
      <sequence>
        <sequence>
          <uuid value="0x0100"/> <!-- L2CAP -->
          <uint16 value="0x0013"/> <!-- PSM 19 = HID Interrupt -->
        </sequence>
        <sequence>
          <uuid value="0x0011"/> <!-- HIDP -->
        </sequence>
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x0100"> <!-- ServiceName -->
    <text value="Spotifone Keyboard"/>
  </attribute>
  <attribute id="0x0101"> <!-- ServiceDescription -->
    <text value="Bluetooth HID Keyboard"/>
  </attribute>
  <attribute id="0x0102"> <!-- ProviderName -->
    <text value="Spotifone"/>
  </attribute>
  <attribute id="0x0200"> <!-- HIDDeviceReleaseNumber -->
    <uint16 value="0x0100"/>
  </attribute>
  <attribute id="0x0201"> <!-- HIDParserVersion -->
    <uint16 value="0x0111"/>
  </attribute>
  <attribute id="0x0202"> <!-- HIDDeviceSubclass -->
    <uint8 value="0x40"/> <!-- Keyboard -->
  </attribute>
  <attribute id="0x0203"> <!-- HIDCountryCode -->
    <uint8 value="0x00"/>
  </attribute>
  <attribute id="0x0204"> <!-- HIDVirtualCable -->
    <boolean value="true"/>
  </attribute>
  <attribute id="0x0205"> <!-- HIDReconnectInitiate -->
    <boolean value="true"/>
  </attribute>
  <attribute id="0x0206"> <!-- HIDDescriptorList -->
    <sequence>
      <sequence>
        <uint8 value="0x22"/> <!-- Report descriptor type -->
        <text encoding="hex" value="{report_desc_hex}"/>
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x0207"> <!-- HIDLANGIDBaseList -->
    <sequence>
      <sequence>
        <uint16 value="0x0409"/> <!-- English (US) -->
        <uint16 value="0x0100"/>
      </sequence>
    </sequence>
  </attribute>
  <attribute id="0x020b"> <!-- HIDBootDevice -->
    <boolean value="true"/>
  </attribute>
  <attribute id="0x020c"> <!-- HIDSupervisionTimeout -->
    <uint16 value="0x0c80"/> <!-- 3200 = 2 seconds -->
  </attribute>
  <attribute id="0x020d"> <!-- HIDNormallyConnectable -->
    <boolean value="true"/>
  </attribute>
  <attribute id="0x020e"> <!-- HIDProfileVersion -->
    <uint16 value="0x0101"/>
  </attribute>
</record>"""


def build_sdp_record():
    """Build SDP record XML with embedded HID report descriptor."""
    desc_hex = HID_REPORT_DESCRIPTOR.hex()
    return SDP_RECORD_XML.replace('{report_desc_hex}', desc_hex)


def build_hid_report(modifier=0, keycode=0):
    """Build a 10-byte HID keyboard report for Classic BT HID.

    Format: [0xA1, 0x01, modifier, 0x00, key0, key1, key2, key3, key4, key5]
      0xA1 = HIDP DATA|INPUT transaction header
      0x01 = Report ID (keyboard)
      modifier = modifier key bitmap (bit 6 = Right Alt)
      0x00 = reserved byte
      key0-key5 = up to 6 simultaneous key codes
    """
    return bytes([0xA1, 0x01, modifier, 0x00, keycode, 0, 0, 0, 0, 0])


class PairAgent(dbus.service.Object):
    """D-Bus pairing agent for Classic BT. DisplayOnly capability."""

    @dbus.service.method(AGENT_IFACE, in_signature='', out_signature='')
    def Release(self):
        logger.info('AGENT: Release')

    @dbus.service.method(AGENT_IFACE, in_signature='os', out_signature='')
    def AuthorizeService(self, device, uuid):
        logger.info('AGENT: AuthorizeService %s %s', device, uuid)

    @dbus.service.method(AGENT_IFACE, in_signature='o', out_signature='s')
    def RequestPinCode(self, device):
        logger.info('AGENT: RequestPinCode %s', device)
        return '0000'

    @dbus.service.method(AGENT_IFACE, in_signature='o', out_signature='u')
    def RequestPasskey(self, device):
        logger.info('AGENT: RequestPasskey %s', device)
        return dbus.UInt32(0)

    @dbus.service.method(AGENT_IFACE, in_signature='ouq', out_signature='')
    def DisplayPasskey(self, device, passkey, entered):
        logger.info('AGENT: DisplayPasskey %s %06d', device, passkey)
        print('\n*** PAIRING CODE: %06d ***\n' % passkey, flush=True)

    @dbus.service.method(AGENT_IFACE, in_signature='os', out_signature='')
    def DisplayPinCode(self, device, pincode):
        logger.info('AGENT: DisplayPinCode %s %s', device, pincode)

    @dbus.service.method(AGENT_IFACE, in_signature='ou', out_signature='')
    def RequestConfirmation(self, device, passkey):
        logger.info('AGENT: RequestConfirmation %s %06d -> ACCEPT', device, passkey)
        print('\n*** CONFIRM CODE: %06d ***\n' % passkey, flush=True)

    @dbus.service.method(AGENT_IFACE, in_signature='o', out_signature='')
    def RequestAuthorization(self, device):
        logger.info('AGENT: RequestAuthorization %s -> ACCEPT', device)

    @dbus.service.method(AGENT_IFACE, in_signature='', out_signature='')
    def Cancel(self):
        logger.info('AGENT: Cancel')


class HIDProfile(dbus.service.Object):
    """D-Bus profile object for HID.

    BlueZ ProfileManager1 manages L2CAP PSM 17+19 listeners and calls
    NewConnection() when a client connects. We get two calls: one for
    the Control channel (PSM 17) and one for the Interrupt channel (PSM 19).
    """

    def __init__(self, bus, path):
        dbus.service.Object.__init__(self, bus, path)
        self.server = None  # Set by HIDKeyboardServer after construction

    @dbus.service.method(PROFILE_IFACE, in_signature='', out_signature='')
    def Release(self):
        logger.info('PROFILE: Release')

    @dbus.service.method(PROFILE_IFACE, in_signature='oha{sv}', out_signature='')
    def NewConnection(self, device, fd, properties):
        """Called by BlueZ when a client connects to PSM 17 or PSM 19.

        BlueZ passes ownership of the fd. We dup it and wrap in a socket.
        First call is Control (PSM 17), second is Interrupt (PSM 19).
        """
        logger.info('PROFILE: NewConnection from %s (fd=%d) props=%s',
                     device, fd, dict(properties))
        try:
            # Dup the fd — BlueZ closes the original after this method returns
            new_fd = os.dup(fd)
            sock = socket.fromfd(new_fd, AF_BLUETOOTH, socket.SOCK_SEQPACKET, BTPROTO_L2CAP)
            os.close(new_fd)  # fromfd dups internally, close our dup
            sock.setblocking(False)

            if self.server:
                self.server._on_profile_connection(device, sock)
        except Exception as e:
            logger.error('PROFILE: NewConnection error: %s', e)

    @dbus.service.method(PROFILE_IFACE, in_signature='o', out_signature='')
    def RequestDisconnection(self, device):
        logger.info('PROFILE: RequestDisconnection %s', device)
        if self.server:
            self.server._handle_disconnect()


class HIDKeyboardServer:
    """Classic Bluetooth HID Keyboard server.

    Manages:
    - D-Bus pairing agent registration
    - HID SDP record registration via ProfileManager1
    - ProfileManager1 handles L2CAP PSM 17+19 (no manual socket binding)
    - IPC socket for receiving key events from button_listener.py
    - HID report transmission on Interrupt channel
    """

    def __init__(self):
        self.mainloop = None
        self.bus = None
        self.agent = None
        self.profile = None
        self._ctrl_client = None   # Connected control channel
        self._intr_client = None   # Connected interrupt channel
        self._ipc_sock = None      # UNIX IPC socket
        self._modifiers = 0        # Modifier key state bitmap
        self._connected = False
        self._watch_ids = []       # GLib watch IDs for cleanup
        self._connection_count = 0  # Track NewConnection calls per session

    def setup(self):
        """Initialize D-Bus, register agent and HID profile."""
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()

        # Register pairing agent
        self.agent = PairAgent(self.bus, AGENT_PATH)
        agent_mgr = dbus.Interface(
            self.bus.get_object(BLUEZ_SERVICE, '/org/bluez'),
            'org.bluez.AgentManager1'
        )
        agent_mgr.RegisterAgent(AGENT_PATH, 'DisplayOnly')
        agent_mgr.RequestDefaultAgent(AGENT_PATH)
        logger.info('Pairing agent registered (DisplayOnly)')

        # Set adapter discoverable + pairable
        adapter_props = dbus.Interface(
            self.bus.get_object(BLUEZ_SERVICE, '/org/bluez/hci0'),
            'org.freedesktop.DBus.Properties'
        )
        adapter_props.Set('org.bluez.Adapter1', 'Discoverable', dbus.Boolean(True))
        adapter_props.Set('org.bluez.Adapter1', 'Pairable', dbus.Boolean(True))
        adapter_props.Set('org.bluez.Adapter1', 'DiscoverableTimeout', dbus.UInt32(0))
        logger.info('Adapter: discoverable + pairable')

        # Register HID profile via ProfileManager1
        # BlueZ binds L2CAP PSM 17+19 from the SDP record and delivers
        # connections via HIDProfile.NewConnection()
        self.profile = HIDProfile(self.bus, PROFILE_PATH)
        self.profile.server = self  # Back-reference for callbacks
        profile_mgr = dbus.Interface(
            self.bus.get_object(BLUEZ_SERVICE, '/org/bluez'),
            'org.bluez.ProfileManager1'
        )
        sdp_xml = build_sdp_record()
        opts = {
            'ServiceRecord': dbus.String(sdp_xml),
            'Role': dbus.String('server'),
            'RequireAuthentication': dbus.Boolean(False),
            'RequireAuthorization': dbus.Boolean(False),
        }
        profile_mgr.RegisterProfile(PROFILE_PATH, '00001124-0000-1000-8000-00805f9b34fb', opts)
        logger.info('HID profile registered via ProfileManager1 (PSM 17+19 managed by BlueZ)')

    def _on_profile_connection(self, device, sock):
        """Handle a new connection from ProfileManager1.

        First connection = Control channel (PSM 17)
        Second connection = Interrupt channel (PSM 19)
        """
        self._connection_count += 1

        if self._ctrl_client is None:
            # First connection — Control channel
            self._ctrl_client = sock
            logger.info('Control channel connected from %s (connection #%d)',
                        device, self._connection_count)
            # Watch for control data (SET_PROTOCOL etc.)
            wid = GLib.io_add_watch(sock.fileno(), GLib.IO_IN | GLib.IO_HUP, self._on_ctrl_data)
            self._watch_ids.append(wid)
        elif self._intr_client is None:
            # Second connection — Interrupt channel
            self._intr_client = sock
            logger.info('Interrupt channel connected from %s (connection #%d)',
                        device, self._connection_count)
            # Watch for disconnect
            wid = GLib.io_add_watch(sock.fileno(), GLib.IO_HUP, self._on_intr_hup)
            self._watch_ids.append(wid)
        else:
            logger.warning('Unexpected third connection from %s, closing', device)
            sock.close()
            return

        self._check_connected()

    def _check_connected(self):
        """Mark as connected when both channels are established."""
        if self._ctrl_client and self._intr_client:
            self._connected = True
            self._connection_count = 0  # Reset for next session
            logger.info('HID fully connected (Control + Interrupt channels)')

    def _on_ctrl_data(self, fd, condition):
        """Handle data on Control channel (SET_PROTOCOL, etc.)."""
        if condition & GLib.IO_HUP:
            logger.info('Control channel disconnected')
            self._handle_disconnect()
            return False
        try:
            data = self._ctrl_client.recv(64)
            if not data:
                logger.info('Control channel closed')
                self._handle_disconnect()
                return False
            logger.debug('Control data: %s', data.hex())
            # Handle SET_PROTOCOL (0x70) — reply with HANDSHAKE_SUCCESS (0x00)
            if data[0] & 0xF0 == 0x70:
                proto = data[0] & 0x01
                logger.info('SET_PROTOCOL %s', 'Report' if proto else 'Boot')
                try:
                    self._ctrl_client.send(bytes([0x00]))  # HANDSHAKE_SUCCESS
                except Exception:
                    pass
        except socket.error as e:
            if e.errno != errno.EAGAIN:
                logger.error('Control recv error: %s', e)
                self._handle_disconnect()
                return False
        return True

    def _on_intr_hup(self, fd, condition):
        """Handle Interrupt channel disconnect."""
        logger.info('Interrupt channel disconnected')
        self._handle_disconnect()
        return False

    def _handle_disconnect(self):
        """Clean up on HID disconnection."""
        self._connected = False
        self._connection_count = 0
        if self._ctrl_client:
            try:
                self._ctrl_client.close()
            except Exception:
                pass
            self._ctrl_client = None
        if self._intr_client:
            try:
                self._intr_client.close()
            except Exception:
                pass
            self._intr_client = None
        self._modifiers = 0
        logger.info('HID disconnected, waiting for reconnection...')

    def start_ipc_socket(self, path=HID_SOCK_PATH):
        """Start UNIX socket for receiving key events from button_listener."""
        if os.path.exists(path):
            os.unlink(path)
        self._ipc_sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self._ipc_sock.bind(path)
        self._ipc_sock.setblocking(False)
        wid = GLib.io_add_watch(self._ipc_sock.fileno(), GLib.IO_IN, self._on_ipc_data)
        self._watch_ids.append(wid)
        logger.info('IPC socket listening: %s', path)

    def _on_ipc_data(self, fd, condition):
        """Handle incoming IPC key event data."""
        try:
            data = self._ipc_sock.recv(64)
            if len(data) >= 2:
                keycode = data[0]
                pressed = data[1] != 0
                self.send_key_event(keycode, pressed)
                logger.debug('IPC: key=%#x pressed=%s', keycode, pressed)
        except Exception as e:
            logger.error('IPC socket error: %s', e)
        return True

    def send_key_event(self, keycode, pressed):
        """Send HID key event. Tracks modifier state for combos."""
        if 0xE0 <= keycode <= 0xE7:
            # Modifier key
            bit = 1 << (keycode - 0xE0)
            if pressed:
                self._modifiers |= bit
            else:
                self._modifiers &= ~bit
            self._send_report(self._modifiers, 0)
        else:
            # Regular key
            if pressed:
                self._send_report(self._modifiers, keycode)
            else:
                self._send_report(self._modifiers, 0)

    def _send_report(self, modifier, keycode):
        """Send HID report on Interrupt channel."""
        if not self._connected or not self._intr_client:
            logger.debug('Not connected, dropping report (mod=%#x key=%#x)', modifier, keycode)
            return False
        report = build_hid_report(modifier, keycode)
        try:
            self._intr_client.send(report)
            logger.debug('HID report sent: mod=%#x key=%#x', modifier, keycode)
            return True
        except Exception as e:
            logger.error('Report send error: %s', e)
            self._handle_disconnect()
            return False

    def run(self):
        """Run the GLib main loop."""
        self.mainloop = GLib.MainLoop()
        logger.info('HID keyboard server running. Waiting for connections...')
        self.mainloop.run()

    def stop(self):
        """Clean shutdown."""
        if self.mainloop:
            self.mainloop.quit()
        self._handle_disconnect()
        if self._ipc_sock:
            self._ipc_sock.close()
            if os.path.exists(HID_SOCK_PATH):
                os.unlink(HID_SOCK_PATH)
        logger.info('HID keyboard server stopped')


def main():
    logger.info('Starting Spotifone Classic BT HID Keyboard')

    server = HIDKeyboardServer()

    def signal_handler(sig, frame):
        logger.info('Signal %d received, shutting down', sig)
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    server.setup()
    server.start_ipc_socket()

    # If --test flag, send a test keystroke after 5 seconds
    if '--test' in sys.argv:
        def send_test():
            logger.info('TEST: Sending Right Alt press + release')
            server.send_key_event(0xE6, True)
            GLib.timeout_add(200, lambda: server.send_key_event(0xE6, False) or False)
            return False
        GLib.timeout_add(5000, send_test)

    server.run()


if __name__ == '__main__':
    main()
