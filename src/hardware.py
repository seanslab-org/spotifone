"""
Spotifone Hardware Abstraction

Linux-specific hardware access for Car Thing:
  - InputEventReader: /dev/input/event* button and knob events
  - BlueZHIDClient: HID keyboard reports via BlueZ/uhid
  - ALSAMic: Microphone mute/unmute via amixer/pactl

All classes have clear interfaces suitable for mocking in tests.
"""

import struct
import logging
import subprocess
import os
from typing import Optional
from threading import Thread, Event

logger = logging.getLogger(__name__)

# Linux input_event struct: timeval (2 longs) + type (u16) + code (u16) + value (u32)
EVENT_FORMAT = 'llHHI'
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)

# Event types
EV_KEY = 1
EV_REL = 2

# Key event values
KEY_RELEASE = 0
KEY_PRESS = 1
KEY_HOLD = 2  # auto-repeat, ignored

# Car Thing keycodes → logical names
BUTTON_KEYCODES = {
    2: 'preset_1',
    3: 'preset_2',
    4: 'preset_3',
    5: 'preset_4',
    50: 'm',
    28: 'enter',
    1: 'esc',
}

# Knob rotation (EV_REL, code=6)
KNOB_LEFT = 4294967295  # -1 as unsigned 32-bit
KNOB_RIGHT = 1

# Logical name → ButtonHandler button ID
BUTTON_NAME_TO_ID = {
    'preset_1': 1,  # Round button area → PTT
    'enter': 2,     # Knob click
    'esc': 3,       # Side button
    'preset_2': 4,
    'preset_3': 5,
    'preset_4': 6,
    'm': 7,
}


class InputEventReader:
    """Reads Linux input events from /dev/input/event* devices."""

    DEV_BUTTONS = '/dev/input/event0'
    DEV_KNOB = '/dev/input/event1'

    def __init__(self, button_callback=None, knob_callback=None):
        """
        Args:
            button_callback: fn(name: str, pressed: bool)
            knob_callback: fn(direction: str)  — 'left' or 'right'
        """
        self.button_callback = button_callback
        self.knob_callback = knob_callback
        self._stop = Event()
        self._threads = []

    def start(self):
        self._stop.clear()
        for dev in (self.DEV_BUTTONS, self.DEV_KNOB):
            if os.path.exists(dev):
                t = Thread(target=self._read_loop, args=(dev,), daemon=True)
                self._threads.append(t)
                t.start()
                logger.info(f"Listening on {dev}")
            else:
                logger.warning(f"Device not found: {dev}")

    def stop(self):
        self._stop.set()
        self._threads.clear()

    def _read_loop(self, path: str):
        try:
            with open(path, 'rb') as f:
                while not self._stop.is_set():
                    data = f.read(EVENT_SIZE)
                    if not data or len(data) < EVENT_SIZE:
                        break
                    self._dispatch(data)
        except PermissionError:
            logger.error(f"Permission denied: {path}")
        except Exception as e:
            logger.error(f"Error reading {path}: {e}")

    def _dispatch(self, data: bytes):
        _sec, _usec, etype, code, value = struct.unpack(EVENT_FORMAT, data)

        if etype == EV_KEY and code in BUTTON_KEYCODES:
            name = BUTTON_KEYCODES[code]
            if value == KEY_PRESS and self.button_callback:
                self.button_callback(name, True)
            elif value == KEY_RELEASE and self.button_callback:
                self.button_callback(name, False)

        elif etype == EV_REL and code == 6 and self.knob_callback:
            if value == KNOB_RIGHT:
                self.knob_callback('right')
            elif value == KNOB_LEFT:
                self.knob_callback('left')


class BlueZHIDClient:
    """HID keyboard report sender via /dev/uhid or external daemon.

    Implements the send_key_event(key_code, pressed) interface
    expected by HIDService.
    """

    REPORT_SIZE = 8

    def __init__(self):
        self._connected = False

    def setup(self):
        if os.path.exists('/dev/uhid'):
            logger.info("uhid available")
        else:
            logger.warning("/dev/uhid not found")

    def send_key_event(self, key_code: int, pressed: bool) -> bytes:
        if pressed:
            report = self._build_report(key=key_code)
        else:
            report = self._build_report()  # empty = release
        logger.debug(f"HID report: {report.hex()}")
        return report

    def _build_report(self, modifier: int = 0, key: int = 0) -> bytes:
        mod_byte = 0
        key_byte = 0
        if 0xE0 <= key <= 0xE7:
            mod_byte = 1 << (key - 0xE0)
        else:
            key_byte = key
        return struct.pack('BBBBBBBB', mod_byte, 0, key_byte, 0, 0, 0, 0, 0)


class ALSAMic:
    """Microphone control via pactl (PulseAudio) or amixer (ALSA)."""

    def __init__(self):
        self._source = None
        self._muted = True

    def find_source(self) -> Optional[str]:
        try:
            result = subprocess.run(
                ['pactl', 'list', 'sources', 'short'],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().split('\n'):
                if line and 'input' in line.lower():
                    self._source = line.split('\t')[1]
                    logger.info(f"Found mic: {self._source}")
                    return self._source
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.error(f"pactl unavailable: {e}")
        return None

    def mute(self):
        if self._source:
            subprocess.run(
                ['pactl', 'set-source-mute', self._source, '1'],
                capture_output=True, timeout=5
            )
        self._muted = True

    def unmute(self):
        if self._source:
            subprocess.run(
                ['pactl', 'set-source-mute', self._source, '0'],
                capture_output=True, timeout=5
            )
        self._muted = False

    @property
    def is_muted(self) -> bool:
        return self._muted
