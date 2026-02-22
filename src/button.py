"""
Spotifone Button Handler

State machine for Car Thing button press/hold/click detection.
Buttons produce three event types:
  - "press"  — emitted immediately on button down
  - "click"  — emitted on release if held < hold_threshold
  - "hold"   — emitted on release if held >= hold_threshold
"""

import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ButtonState:
    """Per-button state machine for press/hold detection."""

    def __init__(self, hold_threshold: float = 0.5):
        self.hold_threshold = hold_threshold
        self.is_pressed = False
        self.press_time = None

    def press(self) -> str:
        self.is_pressed = True
        self.press_time = time.time()
        return "press"

    def release(self) -> Optional[str]:
        if not self.is_pressed:
            return None
        self.is_pressed = False
        duration = time.time() - self.press_time
        return "hold" if duration >= self.hold_threshold else "click"


class ButtonHandler:
    """Manages multiple buttons with per-button callbacks.

    Car Thing buttons:
      1 = Round (PTT)
      2 = Dial click
      3 = Top preset
      4 = Bottom preset
    """

    BUTTON_ROUND = 1
    BUTTON_DIAL = 2
    BUTTON_TOP = 3
    BUTTON_BOTTOM = 4

    def __init__(self, hold_threshold: float = 0.5):
        self._buttons: dict[int, ButtonState] = {}
        self._callbacks: dict[int, callable] = {}
        self._hold_threshold = hold_threshold

    def register(self, button_id: int, callback=None):
        """Register a button with optional callback(button_id, event_type)."""
        self._buttons[button_id] = ButtonState(self._hold_threshold)
        if callback:
            self._callbacks[button_id] = callback

    def on_event(self, button_id: int, pressed: bool) -> Optional[str]:
        """Process a raw button event. Returns event type or None."""
        if button_id not in self._buttons:
            self.register(button_id)

        button = self._buttons[button_id]
        result = button.press() if pressed else button.release()

        if result and button_id in self._callbacks:
            self._callbacks[button_id](button_id, result)

        return result

    def is_pressed(self, button_id: int) -> bool:
        """Check if a button is currently held down."""
        if button_id in self._buttons:
            return self._buttons[button_id].is_pressed
        return False
