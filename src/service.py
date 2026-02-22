"""
Spotifone Service Orchestrator

Single top-level service that wires:
  ButtonHandler → HIDService (keyboard) + PTTController (audio)

PTT flow:
  button press  → HID key down + mic unmute
  button release → HID key up + mic mute (click or hold)
"""

import logging

from button import ButtonHandler
from hid import HIDService
from audio import AudioService, PTTController

logger = logging.getLogger(__name__)


class SpotifoneService:
    """Main service integrating button, HID, and audio."""

    BUTTON_PTT = ButtonHandler.BUTTON_ROUND  # Round button = PTT

    def __init__(self, platform: str = "mac"):
        self.platform = platform
        self.buttons = ButtonHandler(hold_threshold=0.5)
        self.hid = HIDService(platform)
        self.audio = AudioService()
        self.ptt = PTTController(self.audio)
        self.running = False
        self.paired = False

        self.buttons.register(self.BUTTON_PTT, self._on_ptt)

    def _on_ptt(self, button_id: int, event_type: str):
        """Route PTT events to HID and audio."""
        logger.info(f"PTT: {event_type}")
        self.hid.handle_ptt(event_type)
        if event_type in ("press", "hold"):
            self.ptt.on_press()
        else:
            self.ptt.on_release()

    def start(self) -> bool:
        if not self.audio.start():
            return False
        self.hid.connect()
        self.running = True
        logger.info("Spotifone started")
        return True

    def stop(self):
        self.running = False
        self.audio.stop()
        self.hid.disconnect()
        logger.info("Spotifone stopped")

    def pair(self, device_address: str) -> bool:
        if not self.audio.connect(device_address):
            return False
        self.paired = True
        return True

    def unpair(self):
        self.audio.disconnect()
        self.paired = False

    def button_event(self, pressed: bool):
        """Inject a PTT button event (used by hardware layer and tests)."""
        self.buttons.on_event(self.BUTTON_PTT, pressed)

    def status(self) -> dict:
        return {
            "running": self.running,
            "paired": self.paired,
            "platform": self.platform,
            "audio": self.audio.status(),
            "ptt_pressed": self.ptt.pressed,
        }
