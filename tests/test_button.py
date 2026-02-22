"""Tests for Spotifone button state machine."""

import time
import unittest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from button import ButtonState, ButtonHandler


class TestButtonState(unittest.TestCase):

    def test_initial_state(self):
        b = ButtonState()
        self.assertFalse(b.is_pressed)
        self.assertIsNone(b.press_time)

    def test_press_returns_press(self):
        b = ButtonState()
        self.assertEqual(b.press(), "press")
        self.assertTrue(b.is_pressed)

    def test_click_on_short_release(self):
        b = ButtonState(hold_threshold=0.5)
        b.press()
        time.sleep(0.05)
        self.assertEqual(b.release(), "click")
        self.assertFalse(b.is_pressed)

    def test_hold_on_long_release(self):
        b = ButtonState(hold_threshold=0.1)
        b.press()
        time.sleep(0.15)
        self.assertEqual(b.release(), "hold")

    def test_release_without_press_returns_none(self):
        b = ButtonState()
        self.assertIsNone(b.release())

    def test_multiple_press_release_cycles(self):
        b = ButtonState()
        b.press()
        b.release()
        self.assertFalse(b.is_pressed)
        b.press()
        self.assertTrue(b.is_pressed)


class TestButtonHandler(unittest.TestCase):

    def test_register_creates_state(self):
        h = ButtonHandler()
        h.register(1)
        self.assertFalse(h.is_pressed(1))

    def test_auto_register_on_event(self):
        h = ButtonHandler()
        h.on_event(99, True)
        self.assertTrue(h.is_pressed(99))

    def test_click_callback(self):
        events = []
        h = ButtonHandler(hold_threshold=0.5)
        h.register(1, callback=lambda bid, evt: events.append((bid, evt)))
        h.on_event(1, True)
        time.sleep(0.05)
        h.on_event(1, False)
        self.assertEqual(events, [(1, "press"), (1, "click")])

    def test_hold_callback(self):
        events = []
        h = ButtonHandler(hold_threshold=0.1)
        h.register(1, callback=lambda bid, evt: events.append((bid, evt)))
        h.on_event(1, True)
        time.sleep(0.15)
        h.on_event(1, False)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1], (1, "hold"))

    def test_is_pressed_tracks_state(self):
        h = ButtonHandler()
        self.assertFalse(h.is_pressed(1))
        h.on_event(1, True)
        self.assertTrue(h.is_pressed(1))
        h.on_event(1, False)
        self.assertFalse(h.is_pressed(1))

    def test_unregistered_button_not_pressed(self):
        h = ButtonHandler()
        self.assertFalse(h.is_pressed(42))

    def test_multiple_buttons_independent(self):
        h = ButtonHandler()
        h.on_event(1, True)
        h.on_event(2, True)
        self.assertTrue(h.is_pressed(1))
        self.assertTrue(h.is_pressed(2))
        h.on_event(1, False)
        self.assertFalse(h.is_pressed(1))
        self.assertTrue(h.is_pressed(2))

    def test_constants(self):
        self.assertEqual(ButtonHandler.BUTTON_ROUND, 1)
        self.assertEqual(ButtonHandler.BUTTON_DIAL, 2)


if __name__ == "__main__":
    unittest.main()
