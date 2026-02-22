"""Tests for Spotifone service orchestrator."""

import unittest
import time

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from service import SpotifoneService
from hid import KeyCodes


class TestSpotifoneService(unittest.TestCase):

    def setUp(self):
        self.svc = SpotifoneService(platform="mac")

    def test_initial_state(self):
        self.assertFalse(self.svc.running)
        self.assertFalse(self.svc.paired)
        self.assertEqual(self.svc.platform, "mac")

    def test_start_stop(self):
        self.assertTrue(self.svc.start())
        self.assertTrue(self.svc.running)
        self.svc.stop()
        self.assertFalse(self.svc.running)

    def test_pair_unpair(self):
        self.assertTrue(self.svc.pair("AA:BB:CC:DD:EE:FF"))
        self.assertTrue(self.svc.paired)
        self.svc.unpair()
        self.assertFalse(self.svc.paired)

    def test_ptt_press_unmutes_audio(self):
        self.svc.start()
        self.assertFalse(self.svc.audio.active)
        self.svc.button_event(True)
        self.assertTrue(self.svc.audio.active)
        self.assertTrue(self.svc.ptt.pressed)

    def test_ptt_release_mutes_audio(self):
        self.svc.start()
        self.svc.button_event(True)
        self.svc.button_event(False)
        self.assertFalse(self.svc.audio.active)
        self.assertFalse(self.svc.ptt.pressed)

    def test_ptt_sends_hid_key(self):
        self.svc.start()
        self.svc.button_event(True)
        self.assertIn(KeyCodes.RIGHT_ALT, self.svc.hid.pressed_keys)
        self.svc.button_event(False)
        self.assertNotIn(KeyCodes.RIGHT_ALT, self.svc.hid.pressed_keys)

    def test_stop_disconnects_hid(self):
        self.svc.start()
        self.svc.button_event(True)
        self.svc.stop()
        self.assertEqual(len(self.svc.hid.pressed_keys), 0)
        self.assertFalse(self.svc.hid.connected)

    def test_status(self):
        self.svc.start()
        self.svc.pair("AA:BB:CC:DD:EE:FF")
        s = self.svc.status()
        self.assertTrue(s["running"])
        self.assertTrue(s["paired"])
        self.assertEqual(s["platform"], "mac")
        self.assertIn("audio", s)
        self.assertFalse(s["ptt_pressed"])


class TestSpotifoneServicePlatform(unittest.TestCase):

    def test_mac_uses_right_alt(self):
        svc = SpotifoneService(platform="mac")
        key = svc.hid._mapper.get_key("ptt")
        self.assertEqual(key, KeyCodes.RIGHT_ALT)

    def test_windows_uses_right_alt(self):
        svc = SpotifoneService(platform="windows")
        key = svc.hid._mapper.get_key("ptt")
        self.assertEqual(key, KeyCodes.RIGHT_ALT)


if __name__ == "__main__":
    unittest.main()
