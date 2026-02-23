#!/usr/bin/env python3
"""Listens for Car Thing button #1 and sends PTT commands.

Dual-target:
  - HID socket: sends key press/release for BLE HID keyboard emulation
  - Mic socket: sends start/stop streaming commands to mic_bridge daemon
"""
import struct
import socket
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
logger = logging.getLogger('button')

EVENT_FORMAT = 'llHHI'
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)
EV_KEY = 1
PRESET_1_CODE = 2  # Button #1 (preset_1)

# HID keycodes
KEY_A = 0x04        # Letter 'a' — for testing visibility
RIGHT_ALT = 0xE6   # Right Alt/Option — production PTT key
SEND_KEY = RIGHT_ALT

# Socket paths
HID_SOCK_PATH = '/tmp/spotifone_hid.sock'
MIC_SOCK_PATH = '/tmp/spotifone_mic.sock'

# mic_bridge control commands (match CMD_* in mic_bridge.c)
CMD_STOP_STREAMING = 0x00
CMD_START_STREAMING = 0x01


def main():
    hid_sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    mic_sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)

    logger.info('Reading /dev/input/event0, key=%#x', SEND_KEY)
    logger.info('HID socket: %s, Mic socket: %s', HID_SOCK_PATH, MIC_SOCK_PATH)

    with open('/dev/input/event0', 'rb') as f:
        while True:
            data = f.read(EVENT_SIZE)
            if not data or len(data) < EVENT_SIZE:
                break
            _sec, _usec, etype, code, value = struct.unpack(EVENT_FORMAT, data)
            if etype == EV_KEY and code == PRESET_1_CODE:
                if value == 1:  # press
                    # Send HID key down
                    try:
                        hid_sock.sendto(bytes([SEND_KEY, 1]), HID_SOCK_PATH)
                    except OSError:
                        pass  # HID socket may not be listening
                    # Start mic streaming
                    try:
                        mic_sock.sendto(bytes([CMD_START_STREAMING]), MIC_SOCK_PATH)
                    except OSError:
                        pass  # mic_bridge may not be running
                    logger.info('Button PRESS -> HID key %#x down + mic START', SEND_KEY)
                elif value == 0:  # release
                    # Send HID key up
                    try:
                        hid_sock.sendto(bytes([SEND_KEY, 0]), HID_SOCK_PATH)
                    except OSError:
                        pass
                    # Stop mic streaming
                    try:
                        mic_sock.sendto(bytes([CMD_STOP_STREAMING]), MIC_SOCK_PATH)
                    except OSError:
                        pass
                    logger.info('Button RELEASE -> HID key %#x up + mic STOP', SEND_KEY)


if __name__ == '__main__':
    main()
