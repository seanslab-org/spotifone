"""
Spotifone HID Keyboard Service

Maps button events to USB HID key codes and sends HID reports.
Supports both BLE and Classic Bluetooth HID transports.

The PTT button sends Right Alt (0xE6) which maps to:
  - Right Option on macOS
  - Right Alt on Windows
"""

import time
import logging

logger = logging.getLogger(__name__)


class KeyCodes:
    """USB HID key codes (modifier range 0xE0–0xE7)."""
    LEFT_CONTROL = 0xE0
    LEFT_SHIFT = 0xE1
    LEFT_ALT = 0xE2
    LEFT_GUI = 0xE3
    RIGHT_CONTROL = 0xE4
    RIGHT_SHIFT = 0xE5
    RIGHT_ALT = 0xE6
    RIGHT_GUI = 0xE7


class HIDKeyMapper:
    """Maps logical actions to HID key codes per platform."""

    _MAPPINGS = {
        "mac": {"ptt": KeyCodes.RIGHT_ALT},
        "windows": {"ptt": KeyCodes.RIGHT_ALT},
    }

    def __init__(self, platform: str = "mac"):
        if platform not in self._MAPPINGS:
            raise ValueError(f"Unknown platform: {platform!r}")
        self._platform = platform

    def get_key(self, action: str) -> int:
        mapping = self._MAPPINGS[self._platform]
        if action not in mapping:
            raise ValueError(f"Unknown action: {action!r}")
        return mapping[action]


class HIDService:
    """Bluetooth HID keyboard service.

    Manages key state and delegates actual transport to an injected client.
    The client must implement send_key_event(key_code: int, pressed: bool).
    """

    def __init__(self, platform: str = "mac"):
        self.platform = platform
        self._mapper = HIDKeyMapper(platform)
        self._client = None
        self.connected = False
        self._pressed_keys: set[int] = set()

    def set_client(self, client):
        """Inject HID transport client."""
        self._client = client

    def connect(self) -> bool:
        self.connected = True
        logger.info("HID service connected")
        return True

    def disconnect(self):
        self.release_all()
        self.connected = False
        logger.info("HID service disconnected")

    def press_key(self, key_code: int) -> bool:
        if not self.connected:
            return False
        if key_code in self._pressed_keys:
            return False
        self._pressed_keys.add(key_code)
        if self._client:
            self._client.send_key_event(key_code, pressed=True)
        return True

    def release_key(self, key_code: int) -> bool:
        if not self.connected:
            return False
        if key_code not in self._pressed_keys:
            return False
        self._pressed_keys.discard(key_code)
        if self._client:
            self._client.send_key_event(key_code, pressed=False)
        return True

    def release_all(self):
        for key in list(self._pressed_keys):
            self.release_key(key)

    @property
    def pressed_keys(self) -> set[int]:
        return set(self._pressed_keys)

    def handle_ptt(self, event_type: str):
        """Handle PTT button event: press/hold keeps key down, click is tap."""
        key = self._mapper.get_key("ptt")
        if event_type in ("press", "hold"):
            self.press_key(key)
        elif event_type == "click":
            self.press_key(key)
            time.sleep(0.05)
            self.release_key(key)
