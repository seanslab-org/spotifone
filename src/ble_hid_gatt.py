"""
Spotifone BLE HID GATT Server

Registers a BLE HID Keyboard service via BlueZ D-Bus API.
Exposes a GATT application with:
  - HID Service (0x1812)
  - HID Information, Report Map, Control Point, Report, Protocol Mode
  - BLE Advertisement as peripheral keyboard

Based on VibeThing's ble_hid_gatt.c, rewritten in Python using python3-dbus.
"""

import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib
import logging
import struct
import socket
import os

logger = logging.getLogger(__name__)

# D-Bus interfaces
BLUEZ_SERVICE = 'org.bluez'
GATT_MANAGER_IFACE = 'org.bluez.GattManager1'
LE_ADV_MANAGER_IFACE = 'org.bluez.LEAdvertisingManager1'
GATT_SERVICE_IFACE = 'org.bluez.GattService1'
GATT_CHRC_IFACE = 'org.bluez.GattCharacteristic1'
GATT_DESC_IFACE = 'org.bluez.GattDescriptor1'
LE_ADV_IFACE = 'org.bluez.LEAdvertisement1'
DBUS_OM_IFACE = 'org.freedesktop.DBus.ObjectManager'
DBUS_PROP_IFACE = 'org.freedesktop.DBus.Properties'

# HID Report Map — standard USB keyboard descriptor
HID_REPORT_MAP = bytes([
    0x05, 0x01,        # Usage Page (Generic Desktop)
    0x09, 0x06,        # Usage (Keyboard)
    0xA1, 0x01,        # Collection (Application)
    0x85, 0x01,        #   Report ID (1)
    # Modifier byte
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

# HID Information: bcdHID=1.11, country=0, flags=0x03 (RemoteWake+NormallyConnectable)
HID_INFO = bytes([0x11, 0x01, 0x00, 0x03])


class Advertisement(dbus.service.Object):
    PATH = '/org/spotifone/advertisement0'

    def __init__(self, bus):
        self.bus = bus
        dbus.service.Object.__init__(self, bus, self.PATH)

    def get_properties(self):
        return {
            LE_ADV_IFACE: {
                'Type': 'peripheral',
                'ServiceUUIDs': dbus.Array(['00001812-0000-1000-8000-00805f9b34fb'], signature='s'),
                'LocalName': dbus.String('Spotifone'),
                'Appearance': dbus.UInt16(0x03C1),  # Keyboard
                'Includes': dbus.Array(['tx-power'], signature='s'),
            }
        }

    @dbus.service.method(DBUS_PROP_IFACE, in_signature='s', out_signature='a{sv}')
    def GetAll(self, interface):
        return self.get_properties().get(interface, {})

    @dbus.service.method(LE_ADV_IFACE, in_signature='', out_signature='')
    def Release(self):
        logger.info('Advertisement released')


class Application(dbus.service.Object):
    PATH = '/org/spotifone'

    def __init__(self, bus):
        self.bus = bus
        dbus.service.Object.__init__(self, bus, self.PATH)
        self.services = []

    def add_service(self, service):
        self.services.append(service)

    @dbus.service.method(DBUS_OM_IFACE, out_signature='a{oa{sa{sv}}}')
    def GetManagedObjects(self):
        objects = {}
        for service in self.services:
            objects[service.get_path()] = service.get_properties()
            for chrc in service.characteristics:
                objects[chrc.get_path()] = chrc.get_properties()
                for desc in chrc.descriptors:
                    objects[desc.get_path()] = desc.get_properties()
        return objects


class Service(dbus.service.Object):
    PATH_BASE = '/org/spotifone/service'

    def __init__(self, bus, index, uuid, primary=True):
        self.path = self.PATH_BASE + str(index)
        self.bus = bus
        self.uuid = uuid
        self.primary = primary
        self.characteristics = []
        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self):
        return {
            GATT_SERVICE_IFACE: {
                'UUID': self.uuid,
                'Primary': self.primary,
                'Characteristics': dbus.Array(
                    [c.get_path() for c in self.characteristics],
                    signature='o'
                ),
            }
        }

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_characteristic(self, chrc):
        self.characteristics.append(chrc)


class Characteristic(dbus.service.Object):
    def __init__(self, bus, index, uuid, flags, service):
        self.path = service.path + '/char' + str(index)
        self.bus = bus
        self.uuid = uuid
        self.flags = flags
        self.service = service
        self.descriptors = []
        self.value = []
        self.notifying = False
        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self):
        return {
            GATT_CHRC_IFACE: {
                'Service': self.service.get_path(),
                'UUID': self.uuid,
                'Flags': self.flags,
                'Descriptors': dbus.Array(
                    [d.get_path() for d in self.descriptors],
                    signature='o'
                ),
            }
        }

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_descriptor(self, desc):
        self.descriptors.append(desc)

    @dbus.service.method(GATT_CHRC_IFACE, in_signature='a{sv}', out_signature='ay')
    def ReadValue(self, options):
        return self.value

    @dbus.service.method(GATT_CHRC_IFACE, in_signature='aya{sv}')
    def WriteValue(self, value, options):
        self.value = value

    @dbus.service.method(GATT_CHRC_IFACE)
    def StartNotify(self):
        self.notifying = True
        logger.info(f'{self.uuid} StartNotify')

    @dbus.service.method(GATT_CHRC_IFACE)
    def StopNotify(self):
        self.notifying = False
        logger.info(f'{self.uuid} StopNotify')

    def send_notification(self, value):
        if not self.notifying:
            return
        self.value = value
        self.PropertiesChanged(GATT_CHRC_IFACE, {'Value': value}, [])

    @dbus.service.signal(DBUS_PROP_IFACE, signature='sa{sv}as')
    def PropertiesChanged(self, interface, changed, invalidated):
        pass


class Descriptor(dbus.service.Object):
    def __init__(self, bus, index, uuid, flags, chrc):
        self.path = chrc.path + '/desc' + str(index)
        self.bus = bus
        self.uuid = uuid
        self.flags = flags
        self.chrc = chrc
        self.value = []
        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self):
        return {
            GATT_DESC_IFACE: {
                'Characteristic': self.chrc.get_path(),
                'UUID': self.uuid,
                'Flags': self.flags,
            }
        }

    def get_path(self):
        return dbus.ObjectPath(self.path)

    @dbus.service.method(GATT_DESC_IFACE, in_signature='a{sv}', out_signature='ay')
    def ReadValue(self, options):
        return self.value


class HIDService(Service):
    """BLE HID Keyboard GATT Service."""
    UUID = '00001812-0000-1000-8000-00805f9b34fb'

    def __init__(self, bus, index):
        super().__init__(bus, index, self.UUID, primary=True)

        # HID Information (read)
        self.info_chrc = Characteristic(
            bus, 0, '00002a4a-0000-1000-8000-00805f9b34fb',
            ['read'], self)
        self.info_chrc.value = dbus.Array(HID_INFO, signature='y')
        self.add_characteristic(self.info_chrc)

        # Report Map (read)
        self.map_chrc = Characteristic(
            bus, 1, '00002a4b-0000-1000-8000-00805f9b34fb',
            ['read'], self)
        self.map_chrc.value = dbus.Array(HID_REPORT_MAP, signature='y')
        self.add_characteristic(self.map_chrc)

        # Control Point (write-without-response)
        self.ctrl_chrc = Characteristic(
            bus, 2, '00002a4c-0000-1000-8000-00805f9b34fb',
            ['write-without-response'], self)
        self.add_characteristic(self.ctrl_chrc)

        # Report (read + notify) — this sends key events
        self.report_chrc = Characteristic(
            bus, 3, '00002a4d-0000-1000-8000-00805f9b34fb',
            ['read', 'notify'], self)
        self.report_chrc.value = dbus.Array([0]*8, signature='y')
        self.add_characteristic(self.report_chrc)

        # Report Reference descriptor (Report ID=1, Input)
        report_ref = Descriptor(
            bus, 0, '00002908-0000-1000-8000-00805f9b34fb',
            ['read'], self.report_chrc)
        report_ref.value = dbus.Array([0x01, 0x01], signature='y')  # ID=1, Input
        self.report_chrc.add_descriptor(report_ref)

        # Protocol Mode (read + write-without-response)
        self.proto_chrc = Characteristic(
            bus, 4, '00002a4e-0000-1000-8000-00805f9b34fb',
            ['read', 'write-without-response'], self)
        self.proto_chrc.value = dbus.Array([0x01], signature='y')  # Report mode
        self.add_characteristic(self.proto_chrc)

    def send_key(self, modifier=0, keycode=0):
        """Send a HID keyboard report via GATT notification.

        Report format (8 bytes, NO Report ID prefix):
          [modifier, reserved, key1, key2, key3, key4, key5, key6]
        Report ID is in the Report Reference descriptor, not the data.
        """
        report = dbus.Array(
            [modifier, 0x00, keycode, 0, 0, 0, 0, 0],
            signature='y'
        )
        self.report_chrc.send_notification(report)

    def send_release(self):
        """Send key release (all zeros)."""
        report = dbus.Array([0]*8, signature='y')
        self.report_chrc.send_notification(report)


class BLEHIDServer:
    """BLE HID GATT server manager."""

    def __init__(self):
        self.mainloop = None
        self.app = None
        self.adv = None
        self.hid_service = None
        self.bus = None

    def setup(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        self.app = Application(self.bus)
        self.hid_service = HIDService(self.bus, 0)
        self.app.add_service(self.hid_service)
        self.adv = Advertisement(self.bus)

    def register(self):
        adapter = dbus.Interface(
            self.bus.get_object(BLUEZ_SERVICE, '/org/bluez/hci0'),
            GATT_MANAGER_IFACE
        )
        adv_manager = dbus.Interface(
            self.bus.get_object(BLUEZ_SERVICE, '/org/bluez/hci0'),
            LE_ADV_MANAGER_IFACE
        )

        adapter.RegisterApplication(
            self.app.PATH, {},
            reply_handler=lambda: logger.info('GATT application registered'),
            error_handler=lambda e: logger.error(f'GATT registration failed: {e}')
        )

        adv_manager.RegisterAdvertisement(
            self.adv.PATH, {},
            reply_handler=lambda: logger.info('Advertisement registered'),
            error_handler=lambda e: logger.error(f'Advertisement registration failed: {e}')
        )

    def run(self):
        self.mainloop = GLib.MainLoop()
        self.mainloop.run()

    def stop(self):
        if self.mainloop:
            self.mainloop.quit()
        if hasattr(self, '_sock') and self._sock:
            self._sock.close()

    def send_key_event(self, keycode, pressed):
        """Send HID key event. Called from button handler."""
        if not self.hid_service:
            return
        if pressed:
            if 0xE0 <= keycode <= 0xE7:
                modifier = 1 << (keycode - 0xE0)
                self.hid_service.send_key(modifier=modifier)
            else:
                self.hid_service.send_key(keycode=keycode)
        else:
            self.hid_service.send_release()

    def start_socket(self, path='/tmp/spotifone_hid.sock'):
        """Start UNIX socket for receiving key events from other processes."""
        if os.path.exists(path):
            os.unlink(path)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self._sock.bind(path)
        self._sock.setblocking(False)
        GLib.io_add_watch(self._sock.fileno(), GLib.IO_IN, self._on_socket_data)
        logger.info(f'IPC socket listening: {path}')

    def _on_socket_data(self, fd, condition):
        try:
            data = self._sock.recv(64)
            if len(data) >= 2:
                keycode = data[0]
                pressed = data[1] != 0
                self.send_key_event(keycode, pressed)
                logger.debug(f'Socket: key={keycode:#x} pressed={pressed}')
        except Exception as e:
            logger.error(f'Socket error: {e}')
        return True


def main():
    """Standalone GATT server for testing."""
    import sys
    logging.basicConfig(level=logging.DEBUG)
    logger.info('Starting Spotifone BLE HID GATT server')

    server = BLEHIDServer()
    server.setup()
    server.register()
    server.start_socket()

    # If --test flag, send a test keystroke after 5 seconds
    if '--test' in sys.argv:
        def send_test():
            logger.info('TEST: Sending Right Alt press + release')
            server.send_key_event(0xE6, True)
            GLib.timeout_add(200, lambda: server.send_key_event(0xE6, False) or False)
            return False
        GLib.timeout_add(5000, send_test)

    logger.info('GATT server running. Ctrl+C to stop.')
    try:
        server.run()
    except KeyboardInterrupt:
        server.stop()


if __name__ == '__main__':
    main()
