"""Tests for Spotifone HID keyboard service."""

import unittest
import time

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from hid import KeyCodes, HIDKeyMapper, HIDService


class MockHIDClient:
    """Records send_key_event calls for test assertions."""
    def __init__(self):
        self.events = []

    def send_key_event(self, key_code: int, pressed: bool):
        self.events.append({"key": key_code, "pressed": pressed})


class TestKeyCodes(unittest.TestCase):

    def test_right_alt_value(self):
        self.assertEqual(KeyCodes.RIGHT_ALT, 0xE6)

    def test_modifier_range(self):
        for attr in ("LEFT_CONTROL", "LEFT_SHIFT", "LEFT_ALT", "LEFT_GUI",
                      "RIGHT_CONTROL", "RIGHT_SHIFT", "RIGHT_ALT", "RIGHT_GUI"):
            val = getattr(KeyCodes, attr)
            self.assertTrue(0xE0 <= val <= 0xE7, f"{attr}=0x{val:02x} out of range")


class TestHIDKeyMapper(unittest.TestCase):

    def test_mac_ptt(self):
        m = HIDKeyMapper("mac")
        self.assertEqual(m.get_key("ptt"), KeyCodes.RIGHT_ALT)

    def test_windows_ptt(self):
        m = HIDKeyMapper("windows")
        self.assertEqual(m.get_key("ptt"), KeyCodes.RIGHT_ALT)

    def test_invalid_platform(self):
        with self.assertRaises(ValueError):
            HIDKeyMapper("linux")

    def test_invalid_action(self):
        m = HIDKeyMapper("mac")
        with self.assertRaises(ValueError):
            m.get_key("nonexistent")


class TestHIDService(unittest.TestCase):

    def setUp(self):
        self.svc = HIDService(platform="mac")
        self.client = MockHIDClient()
        self.svc.set_client(self.client)

    def test_initial_state(self):
        self.assertFalse(self.svc.connected)
        self.assertEqual(len(self.svc.pressed_keys), 0)

    def test_connect_disconnect(self):
        self.svc.connect()
        self.assertTrue(self.svc.connected)
        self.svc.disconnect()
        self.assertFalse(self.svc.connected)

    def test_press_key(self):
        self.svc.connect()
        self.assertTrue(self.svc.press_key(KeyCodes.RIGHT_ALT))
        self.assertIn(KeyCodes.RIGHT_ALT, self.svc.pressed_keys)
        self.assertEqual(len(self.client.events), 1)
        self.assertTrue(self.client.events[0]["pressed"])

    def test_release_key(self):
        self.svc.connect()
        self.svc.press_key(KeyCodes.RIGHT_ALT)
        self.assertTrue(self.svc.release_key(KeyCodes.RIGHT_ALT))
        self.assertNotIn(KeyCodes.RIGHT_ALT, self.svc.pressed_keys)
        self.assertEqual(len(self.client.events), 2)
        self.assertFalse(self.client.events[1]["pressed"])

    def test_press_not_connected(self):
        self.assertFalse(self.svc.press_key(KeyCodes.RIGHT_ALT))

    def test_release_not_connected(self):
        self.assertFalse(self.svc.release_key(KeyCodes.RIGHT_ALT))

    def test_duplicate_press_ignored(self):
        self.svc.connect()
        self.svc.press_key(KeyCodes.RIGHT_ALT)
        self.assertFalse(self.svc.press_key(KeyCodes.RIGHT_ALT))
        self.assertEqual(len(self.client.events), 1)

    def test_release_unpressed_key_ignored(self):
        self.svc.connect()
        self.assertFalse(self.svc.release_key(KeyCodes.RIGHT_ALT))

    def test_release_all(self):
        self.svc.connect()
        self.svc.press_key(KeyCodes.RIGHT_ALT)
        self.svc.press_key(KeyCodes.LEFT_CONTROL)
        self.svc.release_all()
        self.assertEqual(len(self.svc.pressed_keys), 0)

    def test_disconnect_releases_keys(self):
        self.svc.connect()
        self.svc.press_key(KeyCodes.RIGHT_ALT)
        self.svc.disconnect()
        self.assertEqual(len(self.svc.pressed_keys), 0)

    def test_ptt_press(self):
        self.svc.connect()
        self.svc.handle_ptt("press")
        self.assertIn(KeyCodes.RIGHT_ALT, self.svc.pressed_keys)

    def test_ptt_hold(self):
        self.svc.connect()
        self.svc.handle_ptt("hold")
        self.assertIn(KeyCodes.RIGHT_ALT, self.svc.pressed_keys)

    def test_ptt_click(self):
        self.svc.connect()
        self.svc.handle_ptt("click")
        self.assertNotIn(KeyCodes.RIGHT_ALT, self.svc.pressed_keys)
        self.assertEqual(len(self.client.events), 2)  # press + release

    def test_no_client_no_crash(self):
        svc = HIDService()
        svc.connect()
        svc.press_key(KeyCodes.RIGHT_ALT)
        svc.release_key(KeyCodes.RIGHT_ALT)


if __name__ == "__main__":
    unittest.main()
