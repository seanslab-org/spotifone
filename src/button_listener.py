#!/usr/bin/env python3
"""Listens for Car Thing buttons and sends HID/mic commands.

Button mapping:
  - Function button (mute, code 50): Hold = Right Alt/Option down + mic START,
    release = Right Alt/Option up + mic STOP. PTT trigger.
  - Preset #1 (code 2): Press = send '?' character (Shift + /).
"""
import struct
import socket
import time
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
logger = logging.getLogger('button')

EVENT_FORMAT = 'llHHI'
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)
EV_KEY = 1

# Car Thing button codes (from device tree gpio-keys)
FUNC_BUTTON_CODE = 50   # Function button (DT node: 'mute', KEY_M)
PRESET_1_CODE = 2        # Preset #1 (DT node: 'preset1', KEY_1)

# HID keycodes
RIGHT_ALT = 0xE6    # Right Alt/Option — PTT key
LEFT_SHIFT = 0xE1   # Left Shift — for '?' combo
SLASH = 0x38         # / and ? key (USB HID usage 0x38)

# Socket paths
HID_SOCK_PATH = '/tmp/spotifone_hid.sock'
MIC_SOCK_PATH = '/tmp/spotifone_mic.sock'

# mic_bridge control commands (match CMD_* in mic_bridge.c)
CMD_STOP_STREAMING = 0x00
CMD_START_STREAMING = 0x01


def send_hid(sock, keycode, pressed):
    """Send a HID key event via IPC socket."""
    try:
        sock.sendto(bytes([keycode, 1 if pressed else 0]), HID_SOCK_PATH)
    except OSError:
        pass


def send_mic(sock, cmd):
    """Send a mic command via IPC socket."""
    try:
        sock.sendto(bytes([cmd]), MIC_SOCK_PATH)
    except OSError:
        pass


def send_question_mark(hid_sock):
    """Send '?' character as HID keystrokes (Shift + /)."""
    send_hid(hid_sock, LEFT_SHIFT, True)
    time.sleep(0.01)
    send_hid(hid_sock, SLASH, True)
    time.sleep(0.01)
    send_hid(hid_sock, SLASH, False)
    time.sleep(0.01)
    send_hid(hid_sock, LEFT_SHIFT, False)


def main():
    hid_sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    mic_sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)

    logger.info('Listening on /dev/input/event0')
    logger.info('  Function button (code %d) -> Right Alt/Option + mic PTT',
                FUNC_BUTTON_CODE)
    logger.info('  Preset #1 (code %d) -> "?" character', PRESET_1_CODE)

    with open('/dev/input/event0', 'rb') as f:
        while True:
            data = f.read(EVENT_SIZE)
            if not data or len(data) < EVENT_SIZE:
                break
            _sec, _usec, etype, code, value = struct.unpack(EVENT_FORMAT, data)
            if etype != EV_KEY:
                continue

            if code == FUNC_BUTTON_CODE:
                if value == 1:  # press
                    send_hid(hid_sock, RIGHT_ALT, True)
                    send_mic(mic_sock, CMD_START_STREAMING)
                    logger.info('Function PRESS -> Right Alt down + mic START')
                elif value == 0:  # release
                    send_hid(hid_sock, RIGHT_ALT, False)
                    send_mic(mic_sock, CMD_STOP_STREAMING)
                    logger.info('Function RELEASE -> Right Alt up + mic STOP')

            elif code == PRESET_1_CODE:
                if value == 1:  # press
                    send_question_mark(hid_sock)
                    logger.info('Preset #1 PRESS -> "?" sent')


if __name__ == '__main__':
    main()
