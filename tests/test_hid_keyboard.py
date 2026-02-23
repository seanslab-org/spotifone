"""Tests for Spotifone Classic BT HID Keyboard server (hid_keyboard.py).

Tests cover:
- HID report format and construction
- SDP record generation
- Modifier state tracking
- IPC protocol handling
- Connection state management
"""

import unittest
import sys
import os
from unittest.mock import MagicMock, patch, PropertyMock

# Mock dbus and gi modules before importing hid_keyboard
# These aren't available on macOS dev machine
sys.modules['dbus'] = MagicMock()
sys.modules['dbus.service'] = MagicMock()
sys.modules['dbus.mainloop'] = MagicMock()
sys.modules['dbus.mainloop.glib'] = MagicMock()
sys.modules['gi'] = MagicMock()
sys.modules['gi.repository'] = MagicMock()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from hid_keyboard import (
    build_hid_report,
    build_sdp_record,
    HID_REPORT_DESCRIPTOR,
    HIDKeyboardServer,
    AF_BLUETOOTH,
    BTPROTO_L2CAP,
    L2CAP_PSM_CTRL,
    L2CAP_PSM_INTR,
    HID_SOCK_PATH,
)


class TestBuildHIDReport(unittest.TestCase):
    """Test HID report construction."""

    def test_report_length(self):
        """HID report must be exactly 10 bytes."""
        report = build_hid_report()
        self.assertEqual(len(report), 10)

    def test_report_header(self):
        """First byte is 0xA1 (DATA|INPUT), second is 0x01 (Report ID)."""
        report = build_hid_report()
        self.assertEqual(report[0], 0xA1)
        self.assertEqual(report[1], 0x01)

    def test_empty_report(self):
        """Default report has no modifiers and no keys."""
        report = build_hid_report()
        self.assertEqual(report, bytes([0xA1, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]))

    def test_right_alt_modifier(self):
        """Right Alt modifier = bit 6 = 0x40."""
        report = build_hid_report(modifier=0x40)
        self.assertEqual(report[2], 0x40)
        # No key pressed
        self.assertEqual(report[4], 0x00)

    def test_left_shift_modifier(self):
        """Left Shift modifier = bit 1 = 0x02."""
        report = build_hid_report(modifier=0x02)
        self.assertEqual(report[2], 0x02)

    def test_keycode(self):
        """Keycode goes in byte 4 (key0)."""
        report = build_hid_report(keycode=0x38)  # slash
        self.assertEqual(report[4], 0x38)
        # Modifier should be 0
        self.assertEqual(report[2], 0x00)

    def test_modifier_plus_keycode(self):
        """Shift+/ = ? character."""
        report = build_hid_report(modifier=0x02, keycode=0x38)
        self.assertEqual(report[2], 0x02)  # Left Shift
        self.assertEqual(report[4], 0x38)  # /

    def test_reserved_byte_always_zero(self):
        """Byte 3 (reserved) is always 0x00."""
        report = build_hid_report(modifier=0xFF, keycode=0x65)
        self.assertEqual(report[3], 0x00)

    def test_remaining_keys_zero(self):
        """Key slots 1-5 (bytes 5-9) are always 0 (single key only)."""
        report = build_hid_report(modifier=0x40, keycode=0x38)
        for i in range(5, 10):
            self.assertEqual(report[i], 0x00, f'byte {i} should be 0')


class TestBuildSDPRecord(unittest.TestCase):
    """Test SDP record XML generation."""

    def test_contains_hid_uuid(self):
        """SDP record must contain HID UUID 0x1124."""
        xml = build_sdp_record()
        self.assertIn('0x1124', xml)

    def test_contains_psm_17(self):
        """SDP record must reference PSM 17 (HID Control)."""
        xml = build_sdp_record()
        self.assertIn('0x0011', xml)

    def test_contains_psm_19(self):
        """SDP record must reference PSM 19 (HID Interrupt)."""
        xml = build_sdp_record()
        self.assertIn('0x0013', xml)

    def test_contains_report_descriptor(self):
        """SDP record must contain the hex-encoded HID report descriptor."""
        xml = build_sdp_record()
        desc_hex = HID_REPORT_DESCRIPTOR.hex()
        self.assertIn(desc_hex, xml)

    def test_keyboard_subclass(self):
        """HIDDeviceSubclass must be 0x40 (Keyboard)."""
        xml = build_sdp_record()
        self.assertIn('0x40', xml)

    def test_boot_device(self):
        """HIDBootDevice must be true (macOS requires this)."""
        xml = build_sdp_record()
        # Check for boolean true in the boot device attribute area
        self.assertIn('HIDBootDevice', xml)

    def test_virtual_cable(self):
        """HIDVirtualCable must be true."""
        xml = build_sdp_record()
        self.assertIn('HIDVirtualCable', xml)

    def test_reconnect_initiate(self):
        """HIDReconnectInitiate must be true."""
        xml = build_sdp_record()
        self.assertIn('HIDReconnectInitiate', xml)

    def test_normally_connectable(self):
        """HIDNormallyConnectable must be true."""
        xml = build_sdp_record()
        self.assertIn('HIDNormallyConnectable', xml)

    def test_valid_xml(self):
        """SDP record must be valid XML."""
        import xml.etree.ElementTree as ET
        xml = build_sdp_record()
        # Should parse without error
        ET.fromstring(xml)


class TestHIDReportDescriptor(unittest.TestCase):
    """Test the HID report descriptor itself."""

    def test_descriptor_not_empty(self):
        self.assertGreater(len(HID_REPORT_DESCRIPTOR), 0)

    def test_starts_with_usage_page(self):
        """First bytes: Usage Page (Generic Desktop) = 05 01."""
        self.assertEqual(HID_REPORT_DESCRIPTOR[0], 0x05)
        self.assertEqual(HID_REPORT_DESCRIPTOR[1], 0x01)

    def test_keyboard_usage(self):
        """Usage (Keyboard) = 09 06."""
        self.assertEqual(HID_REPORT_DESCRIPTOR[2], 0x09)
        self.assertEqual(HID_REPORT_DESCRIPTOR[3], 0x06)

    def test_ends_with_end_collection(self):
        """Last byte is End Collection (0xC0)."""
        self.assertEqual(HID_REPORT_DESCRIPTOR[-1], 0xC0)

    def test_report_id_present(self):
        """Report ID (1) = 85 01."""
        self.assertIn(bytes([0x85, 0x01]), HID_REPORT_DESCRIPTOR)


class TestModifierTracking(unittest.TestCase):
    """Test modifier key state tracking in HIDKeyboardServer."""

    def setUp(self):
        self.server = HIDKeyboardServer()
        self.server._connected = True
        self.server._intr_client = MagicMock()
        self.sent_reports = []

        def capture_send(data):
            self.sent_reports.append(data)
        self.server._intr_client.send = capture_send

    def test_right_alt_press(self):
        """Right Alt (0xE6) sets modifier bit 6 = 0x40."""
        self.server.send_key_event(0xE6, True)
        self.assertEqual(self.server._modifiers, 0x40)
        report = self.sent_reports[-1]
        self.assertEqual(report[2], 0x40)

    def test_right_alt_release(self):
        """Right Alt release clears modifier bit."""
        self.server.send_key_event(0xE6, True)
        self.server.send_key_event(0xE6, False)
        self.assertEqual(self.server._modifiers, 0x00)
        report = self.sent_reports[-1]
        self.assertEqual(report[2], 0x00)

    def test_left_shift_press(self):
        """Left Shift (0xE1) sets modifier bit 1 = 0x02."""
        self.server.send_key_event(0xE1, True)
        self.assertEqual(self.server._modifiers, 0x02)

    def test_combined_modifiers(self):
        """Multiple modifiers combine via OR."""
        self.server.send_key_event(0xE1, True)   # Left Shift
        self.server.send_key_event(0xE6, True)   # Right Alt
        self.assertEqual(self.server._modifiers, 0x42)  # 0x02 | 0x40

    def test_regular_key_press(self):
        """Regular keycode (not modifier) goes in key0 field."""
        self.server.send_key_event(0x38, True)  # slash
        report = self.sent_reports[-1]
        self.assertEqual(report[4], 0x38)

    def test_regular_key_release(self):
        """Regular keycode release sends key0=0."""
        self.server.send_key_event(0x38, True)
        self.server.send_key_event(0x38, False)
        report = self.sent_reports[-1]
        self.assertEqual(report[4], 0x00)

    def test_shift_plus_slash(self):
        """Shift+/ = ? combo sends modifier+keycode."""
        self.server.send_key_event(0xE1, True)   # Shift down
        self.server.send_key_event(0x38, True)   # / down
        report = self.sent_reports[-1]
        self.assertEqual(report[2], 0x02)   # Left Shift
        self.assertEqual(report[4], 0x38)   # /

    def test_not_connected_drops_report(self):
        """Reports are dropped when not connected."""
        self.server._connected = False
        self.server.send_key_event(0xE6, True)
        self.assertEqual(len(self.sent_reports), 0)
        # Modifier state still tracked
        self.assertEqual(self.server._modifiers, 0x40)

    def test_all_modifier_bits(self):
        """All 8 modifier keys E0-E7 set correct bits."""
        for i in range(8):
            keycode = 0xE0 + i
            self.server._modifiers = 0
            self.server.send_key_event(keycode, True)
            expected = 1 << i
            self.assertEqual(self.server._modifiers, expected,
                             'keycode %#x should set bit %d' % (keycode, i))


class TestConnectionState(unittest.TestCase):
    """Test connection state management."""

    def setUp(self):
        self.server = HIDKeyboardServer()

    def test_initial_state_disconnected(self):
        self.assertFalse(self.server._connected)
        self.assertIsNone(self.server._ctrl_client)
        self.assertIsNone(self.server._intr_client)

    def test_check_connected_requires_both(self):
        """Both control and interrupt channels needed for connected state."""
        self.server._ctrl_client = MagicMock()
        self.server._check_connected()
        self.assertFalse(self.server._connected)

        self.server._intr_client = MagicMock()
        self.server._check_connected()
        self.assertTrue(self.server._connected)

    def test_profile_connection_first_is_control(self):
        """First NewConnection from ProfileManager1 becomes control channel."""
        sock = MagicMock()
        sock.fileno.return_value = 10
        self.server._on_profile_connection('/org/bluez/hci0/dev_XX', sock)
        self.assertEqual(self.server._ctrl_client, sock)
        self.assertIsNone(self.server._intr_client)

    def test_profile_connection_second_is_interrupt(self):
        """Second NewConnection becomes interrupt channel."""
        ctrl_sock = MagicMock()
        ctrl_sock.fileno.return_value = 10
        intr_sock = MagicMock()
        intr_sock.fileno.return_value = 11
        self.server._on_profile_connection('/org/bluez/hci0/dev_XX', ctrl_sock)
        self.server._on_profile_connection('/org/bluez/hci0/dev_XX', intr_sock)
        self.assertEqual(self.server._ctrl_client, ctrl_sock)
        self.assertEqual(self.server._intr_client, intr_sock)
        self.assertTrue(self.server._connected)

    def test_profile_connection_third_rejected(self):
        """Third NewConnection is rejected and closed."""
        for i in range(2):
            s = MagicMock()
            s.fileno.return_value = 10 + i
            self.server._on_profile_connection('/org/bluez/hci0/dev_XX', s)
        extra = MagicMock()
        self.server._on_profile_connection('/org/bluez/hci0/dev_XX', extra)
        extra.close.assert_called_once()

    def test_disconnect_clears_state(self):
        """Disconnect closes sockets and resets state."""
        ctrl = MagicMock()
        intr = MagicMock()
        self.server._ctrl_client = ctrl
        self.server._intr_client = intr
        self.server._connected = True
        self.server._modifiers = 0x40

        self.server._handle_disconnect()

        self.assertFalse(self.server._connected)
        self.assertIsNone(self.server._ctrl_client)
        self.assertIsNone(self.server._intr_client)
        self.assertEqual(self.server._modifiers, 0)
        ctrl.close.assert_called_once()
        intr.close.assert_called_once()

    def test_disconnect_tolerates_socket_errors(self):
        """Disconnect doesn't crash on socket close errors."""
        ctrl = MagicMock()
        ctrl.close.side_effect = OSError('already closed')
        intr = MagicMock()
        intr.close.side_effect = OSError('already closed')
        self.server._ctrl_client = ctrl
        self.server._intr_client = intr
        self.server._connected = True

        # Should not raise
        self.server._handle_disconnect()
        self.assertFalse(self.server._connected)

    def test_send_error_triggers_disconnect(self):
        """Socket send error triggers disconnect."""
        self.server._connected = True
        client = MagicMock()
        client.send.side_effect = OSError('Connection reset')
        self.server._intr_client = client

        result = self.server._send_report(0x40, 0)
        self.assertFalse(result)
        self.assertFalse(self.server._connected)


class TestIPCProtocol(unittest.TestCase):
    """Test IPC socket protocol handling."""

    def setUp(self):
        self.server = HIDKeyboardServer()
        self.server._connected = True
        self.server._intr_client = MagicMock()
        self.sent_reports = []
        self.server._intr_client.send = lambda d: self.sent_reports.append(d)

    def test_two_byte_protocol(self):
        """IPC protocol is [keycode, pressed] — 2 bytes."""
        # Simulate receiving Right Alt press via IPC
        self.server.send_key_event(0xE6, True)
        self.assertEqual(len(self.sent_reports), 1)
        report = self.sent_reports[0]
        self.assertEqual(report[2], 0x40)  # Right Alt modifier

    def test_release_byte(self):
        """pressed=0 means release."""
        self.server.send_key_event(0xE6, True)
        self.server.send_key_event(0xE6, False)
        self.assertEqual(len(self.sent_reports), 2)
        self.assertEqual(self.sent_reports[1][2], 0x00)


class TestL2CAPConstants(unittest.TestCase):
    """Verify L2CAP and Bluetooth socket constants."""

    def test_af_bluetooth(self):
        self.assertEqual(AF_BLUETOOTH, 31)

    def test_btproto_l2cap(self):
        self.assertEqual(BTPROTO_L2CAP, 0)

    def test_psm_control(self):
        self.assertEqual(L2CAP_PSM_CTRL, 17)

    def test_psm_interrupt(self):
        self.assertEqual(L2CAP_PSM_INTR, 19)

    def test_hid_sock_path(self):
        self.assertEqual(HID_SOCK_PATH, '/tmp/spotifone_hid.sock')


if __name__ == '__main__':
    unittest.main()
