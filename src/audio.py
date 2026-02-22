"""
Spotifone Audio Service

Bluetooth HFP/HSP microphone service with push-to-talk control.
Starts muted — PTT press unmutes, PTT release mutes.
"""

import logging

logger = logging.getLogger(__name__)


class AudioService:
    """Bluetooth audio (microphone) service."""

    def __init__(self):
        self.running = False
        self.muted = True
        self.device: str | None = None

    def start(self) -> bool:
        self.running = True
        logger.info("Audio service started")
        return True

    def stop(self):
        self.running = False
        self.mute()
        logger.info("Audio service stopped")

    def connect(self, device_address: str) -> bool:
        self.device = device_address
        logger.info(f"Audio connected to {device_address}")
        return True

    def disconnect(self):
        self.device = None
        self.mute()
        logger.info("Audio disconnected")

    def unmute(self) -> bool:
        if not self.running:
            return False
        self.muted = False
        logger.debug("Mic unmuted")
        return True

    def mute(self) -> bool:
        self.muted = True
        logger.debug("Mic muted")
        return True

    @property
    def active(self) -> bool:
        """True when mic is unmuted and service is running."""
        return not self.muted and self.running

    def status(self) -> dict:
        return {
            "running": self.running,
            "muted": self.muted,
            "device": self.device,
            "active": self.active,
        }


class PTTController:
    """Push-to-talk controller — bridges button events to audio mute/unmute."""

    def __init__(self, audio: AudioService):
        self._audio = audio
        self.pressed = False

    def on_press(self) -> bool:
        self.pressed = True
        return self._audio.unmute()

    def on_release(self) -> bool:
        self.pressed = False
        return self._audio.mute()
