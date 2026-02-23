#!/usr/bin/env python3
"""Combined BLE HID server + pairing agent for debugging."""
import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib
import sys
import os
import socket
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(name)s %(message)s'
)
logger = logging.getLogger('spotifone')

sys.path.insert(0, '/opt/spotifone/src')
from ble_hid_gatt import (
    Application, HIDService, Advertisement,
    GATT_MANAGER_IFACE, LE_ADV_MANAGER_IFACE
)

AGENT_IFACE = 'org.bluez.Agent1'
AGENT_PATH = '/org/spotifone/agent'


class PairAgent(dbus.service.Object):
    @dbus.service.method(AGENT_IFACE, in_signature='', out_signature='')
    def Release(self):
        logger.info('AGENT: Release')

    @dbus.service.method(AGENT_IFACE, in_signature='os', out_signature='')
    def AuthorizeService(self, device, uuid):
        logger.info(f'AGENT: AuthorizeService {device} {uuid}')

    @dbus.service.method(AGENT_IFACE, in_signature='o', out_signature='s')
    def RequestPinCode(self, device):
        logger.info(f'AGENT: RequestPinCode {device}')
        return '0000'

    @dbus.service.method(AGENT_IFACE, in_signature='o', out_signature='u')
    def RequestPasskey(self, device):
        logger.info(f'AGENT: RequestPasskey {device}')
        return dbus.UInt32(0)

    @dbus.service.method(AGENT_IFACE, in_signature='ouq', out_signature='')
    def DisplayPasskey(self, device, passkey, entered):
        logger.info(f'AGENT: DisplayPasskey {device} {passkey:06d}')
        print(f'\n*** PAIRING CODE: {passkey:06d} ***\n', flush=True)

    @dbus.service.method(AGENT_IFACE, in_signature='os', out_signature='')
    def DisplayPinCode(self, device, pincode):
        logger.info(f'AGENT: DisplayPinCode {device} {pincode}')

    @dbus.service.method(AGENT_IFACE, in_signature='ou', out_signature='')
    def RequestConfirmation(self, device, passkey):
        logger.info(f'AGENT: RequestConfirmation {device} {passkey:06d} -> ACCEPT')
        print(f'\n*** CONFIRM CODE: {passkey:06d} ***\n', flush=True)

    @dbus.service.method(AGENT_IFACE, in_signature='o', out_signature='')
    def RequestAuthorization(self, device):
        logger.info(f'AGENT: RequestAuthorization {device} -> ACCEPT')

    @dbus.service.method(AGENT_IFACE, in_signature='', out_signature='')
    def Cancel(self):
        logger.info('AGENT: Cancel')


def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    # Register pairing agent
    agent = PairAgent(bus, AGENT_PATH)
    mgr = dbus.Interface(
        bus.get_object('org.bluez', '/org/bluez'),
        'org.bluez.AgentManager1'
    )
    mgr.RegisterAgent(AGENT_PATH, 'DisplayOnly')
    mgr.RequestDefaultAgent(AGENT_PATH)
    logger.info('Pairing agent registered (DisplayOnly)')

    # Set adapter properties
    adapter = dbus.Interface(
        bus.get_object('org.bluez', '/org/bluez/hci0'),
        'org.freedesktop.DBus.Properties'
    )
    adapter.Set('org.bluez.Adapter1', 'Discoverable', dbus.Boolean(True))
    adapter.Set('org.bluez.Adapter1', 'Pairable', dbus.Boolean(True))
    adapter.Set('org.bluez.Adapter1', 'DiscoverableTimeout', dbus.UInt32(0))
    logger.info('Adapter: discoverable + pairable')

    # Register GATT application
    app = Application(bus)
    hid_service = HIDService(bus, 0)
    app.add_service(hid_service)

    gatt_mgr = dbus.Interface(
        bus.get_object('org.bluez', '/org/bluez/hci0'),
        GATT_MANAGER_IFACE
    )
    gatt_mgr.RegisterApplication(
        app.PATH, {},
        reply_handler=lambda: logger.info('GATT application registered'),
        error_handler=lambda e: logger.error(f'GATT reg failed: {e}')
    )

    # Register advertisement
    adv = Advertisement(bus)
    adv_mgr = dbus.Interface(
        bus.get_object('org.bluez', '/org/bluez/hci0'),
        LE_ADV_MANAGER_IFACE
    )
    adv_mgr.RegisterAdvertisement(
        adv.PATH, {},
        reply_handler=lambda: logger.info('Advertisement registered'),
        error_handler=lambda e: logger.error(f'Adv failed: {e}')
    )

    logger.info('All services started. Waiting for connections...')

    # IPC socket for receiving key events
    sock_path = '/tmp/spotifone_hid.sock'
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(sock_path)
    sock.setblocking(False)

    def on_socket_data(fd, condition):
        try:
            data = sock.recv(64)
            if len(data) >= 2:
                keycode = data[0]
                pressed = data[1] != 0
                logger.info(f'IPC: key={keycode:#x} pressed={pressed}')
                if pressed:
                    if 0xE0 <= keycode <= 0xE7:
                        modifier = 1 << (keycode - 0xE0)
                        hid_service.send_key(modifier=modifier)
                    else:
                        hid_service.send_key(keycode=keycode)
                else:
                    hid_service.send_release()
        except Exception as e:
            logger.error(f'Socket error: {e}')
        return True

    GLib.io_add_watch(sock.fileno(), GLib.IO_IN, on_socket_data)
    logger.info(f'IPC socket listening: {sock_path}')

    # --test flag: send Right Alt press+release after 3 seconds
    if '--test' in sys.argv:
        def send_test():
            logger.info('TEST: Sending Right Alt (0xE6) press')
            hid_service.send_key(modifier=(1 << (0xE6 - 0xE0)))
            def release():
                logger.info('TEST: Sending Right Alt (0xE6) release')
                hid_service.send_release()
                return False
            GLib.timeout_add(200, release)
            return False
        GLib.timeout_add(3000, send_test)

    # Monitor D-Bus signals for debugging
    def props_changed(interface, changed, invalidated, path=None):
        if 'org.bluez' in str(path) or 'spotifone' in str(path):
            logger.info(
                f'SIGNAL: {path} {interface} '
                f'changed={dict(changed)}'
            )

    bus.add_signal_receiver(
        props_changed,
        dbus_interface='org.freedesktop.DBus.Properties',
        signal_name='PropertiesChanged',
        path_keyword='path'
    )

    mainloop = GLib.MainLoop()
    try:
        mainloop.run()
    except KeyboardInterrupt:
        sock.close()
        if os.path.exists(sock_path):
            os.unlink(sock_path)


if __name__ == '__main__':
    main()
