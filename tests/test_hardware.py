"""Tests for Spotifone hardware abstraction."""

import struct
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from hardware import (
    InputEventReader, BlueZHIDClient, ALSAMic,
    EVENT_FORMAT, EVENT_SIZE,
    EV_KEY, EV_REL, KEY_PRESS, KEY_RELEASE, KEY_HOLD,
    BUTTON_KEYCODES, KNOB_LEFT, KNOB_RIGHT,
    BUTTON_NAME_TO_ID,
)


def make_event(etype, code, value):
    """Create a raw input_event struct."""
    return struct.pack(EVENT_FORMAT, 0, 0, etype, code, value)


# ── InputEventReader ──

class TestInputEventReader(unittest.TestCase):

    def test_button_press(self):
        events = []
        r = InputEventReader(button_callback=lambda n, p: events.append((n, p)))
        r._dispatch(make_event(EV_KEY, 2, KEY_PRESS))
        self.assertEqual(events, [('preset_1', True)])

    def test_button_release(self):
        events = []
        r = InputEventReader(button_callback=lambda n, p: events.append((n, p)))
        r._dispatch(make_event(EV_KEY, 2, KEY_RELEASE))
        self.assertEqual(events, [('preset_1', False)])

    def test_all_keycodes(self):
        for code, name in BUTTON_KEYCODES.items():
            events = []
            r = InputEventReader(button_callback=lambda n, p: events.append((n, p)))
            r._dispatch(make_event(EV_KEY, code, KEY_PRESS))
            self.assertEqual(events[0][0], name, f"keycode {code}")

    def test_auto_repeat_ignored(self):
        events = []
        r = InputEventReader(button_callback=lambda n, p: events.append((n, p)))
        r._dispatch(make_event(EV_KEY, 2, KEY_HOLD))
        self.assertEqual(events, [])

    def test_unknown_keycode_ignored(self):
        events = []
        r = InputEventReader(button_callback=lambda n, p: events.append((n, p)))
        r._dispatch(make_event(EV_KEY, 999, KEY_PRESS))
        self.assertEqual(events, [])

    def test_knob_right(self):
        events = []
        r = InputEventReader(knob_callback=lambda d: events.append(d))
        r._dispatch(make_event(EV_REL, 6, KNOB_RIGHT))
        self.assertEqual(events, ['right'])

    def test_knob_left(self):
        events = []
        r = InputEventReader(knob_callback=lambda d: events.append(d))
        r._dispatch(make_event(EV_REL, 6, KNOB_LEFT))
        self.assertEqual(events, ['left'])

    def test_no_callback_no_crash(self):
        r = InputEventReader()
        r._dispatch(make_event(EV_KEY, 2, KEY_PRESS))
        r._dispatch(make_event(EV_REL, 6, KNOB_RIGHT))

    def test_press_release_sequence(self):
        events = []
        r = InputEventReader(button_callback=lambda n, p: events.append((n, p)))
        r._dispatch(make_event(EV_KEY, 28, KEY_PRESS))
        r._dispatch(make_event(EV_KEY, 28, KEY_RELEASE))
        self.assertEqual(events, [('enter', True), ('enter', False)])

    def test_read_from_file(self):
        events = []
        r = InputEventReader(button_callback=lambda n, p: events.append((n, p)))
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(make_event(EV_KEY, 2, KEY_PRESS))
            f.write(make_event(EV_KEY, 2, KEY_RELEASE))
            path = f.name
        try:
            r._read_loop(path)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0], ('preset_1', True))
            self.assertEqual(events[1], ('preset_1', False))
        finally:
            os.unlink(path)

    def test_m_button(self):
        events = []
        r = InputEventReader(button_callback=lambda n, p: events.append((n, p)))
        r._dispatch(make_event(EV_KEY, 50, KEY_PRESS))
        self.assertEqual(events, [('m', True)])

    def test_esc_button(self):
        events = []
        r = InputEventReader(button_callback=lambda n, p: events.append((n, p)))
        r._dispatch(make_event(EV_KEY, 1, KEY_PRESS))
        self.assertEqual(events, [('esc', True)])


class TestButtonNameMapping(unittest.TestCase):

    def test_preset_1_maps_to_ptt(self):
        self.assertEqual(BUTTON_NAME_TO_ID['preset_1'], 1)

    def test_all_names_have_ids(self):
        for name in BUTTON_KEYCODES.values():
            self.assertIn(name, BUTTON_NAME_TO_ID, f"{name} missing from map")


# ── BlueZHIDClient ──

class TestBlueZHIDClient(unittest.TestCase):

    def test_empty_report(self):
        c = BlueZHIDClient()
        report = c._build_report()
        self.assertEqual(len(report), 8)
        self.assertEqual(report, bytes(8))

    def test_modifier_key_report(self):
        c = BlueZHIDClient()
        report = c._build_report(key=0xE6)  # RIGHT_ALT
        self.assertEqual(report[0], 1 << 6)
        self.assertEqual(report[2], 0)

    def test_regular_key_report(self):
        c = BlueZHIDClient()
        report = c._build_report(key=0x04)  # 'A'
        self.assertEqual(report[0], 0)
        self.assertEqual(report[2], 0x04)

    def test_press_event(self):
        c = BlueZHIDClient()
        report = c.send_key_event(0xE6, pressed=True)
        self.assertNotEqual(report, bytes(8))

    def test_release_event(self):
        c = BlueZHIDClient()
        report = c.send_key_event(0xE6, pressed=False)
        self.assertEqual(report, bytes(8))

    def test_right_alt_bit(self):
        c = BlueZHIDClient()
        report = c.send_key_event(0xE6, pressed=True)
        self.assertEqual(report[0], 64)

    def test_left_control_bit(self):
        c = BlueZHIDClient()
        report = c._build_report(key=0xE0)
        self.assertEqual(report[0], 1)

    def test_report_size(self):
        c = BlueZHIDClient()
        for key in [0x00, 0x04, 0xE0, 0xE6, 0xE7]:
            report = c._build_report(key=key)
            self.assertEqual(len(report), 8)


# ── ALSAMic ──

class TestALSAMic(unittest.TestCase):

    @patch('hardware.subprocess.run')
    def test_find_source(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="0\talsa_input.usb-mic\tmodule-alsa-card.c\ts16le 1ch 16000Hz\tRUNNING\n"
        )
        mic = ALSAMic()
        source = mic.find_source()
        self.assertEqual(source, 'alsa_input.usb-mic')

    @patch('hardware.subprocess.run')
    def test_find_source_no_input(self, mock_run):
        mock_run.return_value = MagicMock(stdout="0\tmonitor\tmod.c\ts16le\tIDLE\n")
        mic = ALSAMic()
        self.assertIsNone(mic.find_source())

    @patch('hardware.subprocess.run')
    def test_mute(self, mock_run):
        mic = ALSAMic()
        mic._source = 'test_src'
        mic.mute()
        mock_run.assert_called_with(
            ['pactl', 'set-source-mute', 'test_src', '1'],
            capture_output=True, timeout=5
        )
        self.assertTrue(mic.is_muted)

    @patch('hardware.subprocess.run')
    def test_unmute(self, mock_run):
        mic = ALSAMic()
        mic._source = 'test_src'
        mic.unmute()
        mock_run.assert_called_with(
            ['pactl', 'set-source-mute', 'test_src', '0'],
            capture_output=True, timeout=5
        )
        self.assertFalse(mic.is_muted)

    def test_mute_without_source(self):
        mic = ALSAMic()
        mic.mute()
        mic.unmute()

    def test_initial_state(self):
        mic = ALSAMic()
        self.assertTrue(mic.is_muted)


if __name__ == '__main__':
    unittest.main()
