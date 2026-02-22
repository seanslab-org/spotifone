"""Tests for Spotifone audio service and PTT controller."""

import unittest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from audio import AudioService, PTTController


class TestAudioService(unittest.TestCase):

    def setUp(self):
        self.svc = AudioService()

    def test_initial_state(self):
        self.assertFalse(self.svc.running)
        self.assertTrue(self.svc.muted)
        self.assertIsNone(self.svc.device)
        self.assertFalse(self.svc.active)

    def test_start_stop(self):
        self.assertTrue(self.svc.start())
        self.assertTrue(self.svc.running)
        self.svc.stop()
        self.assertFalse(self.svc.running)

    def test_stop_mutes(self):
        self.svc.start()
        self.svc.unmute()
        self.svc.stop()
        self.assertTrue(self.svc.muted)

    def test_mute_unmute(self):
        self.svc.start()
        self.assertTrue(self.svc.unmute())
        self.assertFalse(self.svc.muted)
        self.assertTrue(self.svc.mute())
        self.assertTrue(self.svc.muted)

    def test_unmute_requires_running(self):
        self.assertFalse(self.svc.unmute())

    def test_mute_always_succeeds(self):
        self.assertTrue(self.svc.mute())

    def test_active_property(self):
        self.svc.start()
        self.assertFalse(self.svc.active)
        self.svc.unmute()
        self.assertTrue(self.svc.active)
        self.svc.mute()
        self.assertFalse(self.svc.active)

    def test_connect_disconnect(self):
        self.assertTrue(self.svc.connect("AA:BB:CC:DD:EE:FF"))
        self.assertEqual(self.svc.device, "AA:BB:CC:DD:EE:FF")
        self.svc.disconnect()
        self.assertIsNone(self.svc.device)

    def test_disconnect_mutes(self):
        self.svc.start()
        self.svc.unmute()
        self.svc.disconnect()
        self.assertTrue(self.svc.muted)

    def test_status(self):
        self.svc.start()
        self.svc.connect("AA:BB:CC:DD:EE:FF")
        self.svc.unmute()
        s = self.svc.status()
        self.assertTrue(s["running"])
        self.assertFalse(s["muted"])
        self.assertEqual(s["device"], "AA:BB:CC:DD:EE:FF")
        self.assertTrue(s["active"])


class TestPTTController(unittest.TestCase):

    def setUp(self):
        self.audio = AudioService()
        self.audio.start()
        self.ptt = PTTController(self.audio)

    def test_press_unmutes(self):
        self.ptt.on_press()
        self.assertTrue(self.ptt.pressed)
        self.assertTrue(self.audio.active)

    def test_release_mutes(self):
        self.ptt.on_press()
        self.ptt.on_release()
        self.assertFalse(self.ptt.pressed)
        self.assertFalse(self.audio.active)

    def test_press_release_cycle(self):
        self.ptt.on_press()
        self.assertTrue(self.audio.active)
        self.ptt.on_release()
        self.assertFalse(self.audio.active)
        self.ptt.on_press()
        self.assertTrue(self.audio.active)

    def test_release_without_press(self):
        self.ptt.on_release()
        self.assertFalse(self.ptt.pressed)
        self.assertTrue(self.audio.muted)


if __name__ == "__main__":
    unittest.main()
