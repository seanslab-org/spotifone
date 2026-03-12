#!/usr/bin/env python3
"""
Spotifone Phase 2 Menu UI (touch + framebuffer).

Hard constraint: do NOT change existing button/HID behaviors.
Only addition:
  - Mute button (KEY_M / code 50) toggles this menu via /tmp/spotifone_menu.sock.

Interaction is touch-only via /dev/input/event3.
"""

import errno
import logging
import os
import fcntl
import re
import select
import socket
import struct
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


LOG = logging.getLogger("menu_ui")

MENU_SOCK_PATH = "/tmp/spotifone_menu.sock"
MENU_LOG_PATH = "/tmp/menu.log"
TOUCH_DEV = "/dev/input/event3"
FB_DEV = "/dev/fb0"
VERSION_PATH = "/opt/spotifone/VERSION"

LOGO_FB_PATHS = [
    "/opt/spotifone/logo.fb",
    str(Path(__file__).resolve().parent.parent / "logo.fb"),
]

# Linux input_event
EVENT_FORMAT = "llHHI"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)

EV_SYN = 0
EV_KEY = 1
EV_ABS = 3

ABS_MT_POSITION_X = 53
ABS_MT_POSITION_Y = 54
ABS_MT_TRACKING_ID = 57

TRACKING_ID_UP = 0xFFFFFFFF  # -1 as unsigned u32

# Framebuffer constants (Car Thing)
FB_WIDTH = 480
FB_HEIGHT = 800
FB_BPP = 4         # 32-bit BGRA8888
FB_STRIDE = FB_WIDTH * FB_BPP  # 1920 bytes/row

# ioctl numbers (linux/fb.h)
FBIOGET_VSCREENINFO = 0x4600
FBIOPUT_VSCREENINFO = 0x4601
FBIOBLANK = 0x4611  # arg 0 = FB_BLANK_UNBLANK

# Walkie-inspired palette (BGRA8888) with Spotifone purple accent.
# walkie.sh uses: bg=#0a0a0a surface=#141414 border=#222 text=#e0e0e0 muted=#888 accent=#22c55e
PURPLE = (0xCF, 0x56, 0x6E, 0xFF)   # #6E56CF
BG = (0x0A, 0x0A, 0x0A, 0xFF)       # #0a0a0a
SURFACE = (0x14, 0x14, 0x14, 0xFF)  # #141414
BORDER = (0x22, 0x22, 0x22, 0xFF)   # #222222
TEXT = (0xE0, 0xE0, 0xE0, 0xFF)     # #e0e0e0
TEXT_DIM = (0x88, 0x88, 0x88, 0xFF) # #888888

# Status colors
RED = (0x2A, 0x2A, 0xC8, 0xFF)      # muted-ish red
GREEN = (0x2A, 0xC8, 0x2A, 0xFF)    # muted-ish green

HOME_LIVE_SCAN_INTERVAL_S = 20.0
HOME_LIVE_SCAN_TIMEOUT_S = 4
HOME_GRID_COLUMNS = 2
HOME_LEGEND_WIDTH = 72

# IPC commands (1 byte)
CMD_TOGGLE = 0x01


# ──────────────────────────────────────────────────────────────────────────────
# Tiny bitmap font (8x8)
# Public domain-style font table (subset): index by ASCII code.
# For simplicity we include the full 128 table; it's small.
# ──────────────────────────────────────────────────────────────────────────────

# fmt: off
FONT8X8_BASIC = [
    # 0x00-0x1F control (blank)
    [0x00]*8 for _ in range(32)
] + [
    # 0x20 ' '
    [0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00],
    # 0x21 '!'
    [0x18,0x18,0x18,0x18,0x18,0x00,0x18,0x00],
    # 0x22 '"'
    [0x6c,0x6c,0x48,0x00,0x00,0x00,0x00,0x00],
    # 0x23 '#'
    [0x6c,0x6c,0xfe,0x6c,0xfe,0x6c,0x6c,0x00],
    # 0x24 '$'
    [0x18,0x3e,0x60,0x3c,0x06,0x7c,0x18,0x00],
    # 0x25 '%'
    [0x00,0xc6,0xcc,0x18,0x30,0x66,0xc6,0x00],
    # 0x26 '&'
    [0x38,0x6c,0x38,0x76,0xdc,0xcc,0x76,0x00],
    # 0x27 '''
    [0x30,0x30,0x60,0x00,0x00,0x00,0x00,0x00],
    # 0x28 '('
    [0x0c,0x18,0x30,0x30,0x30,0x18,0x0c,0x00],
    # 0x29 ')'
    [0x30,0x18,0x0c,0x0c,0x0c,0x18,0x30,0x00],
    # 0x2A '*'
    [0x00,0x66,0x3c,0xff,0x3c,0x66,0x00,0x00],
    # 0x2B '+'
    [0x00,0x18,0x18,0x7e,0x18,0x18,0x00,0x00],
    # 0x2C ','
    [0x00,0x00,0x00,0x00,0x00,0x18,0x18,0x30],
    # 0x2D '-'
    [0x00,0x00,0x00,0x7e,0x00,0x00,0x00,0x00],
    # 0x2E '.'
    [0x00,0x00,0x00,0x00,0x00,0x18,0x18,0x00],
    # 0x2F '/'
    [0x06,0x0c,0x18,0x30,0x60,0xc0,0x80,0x00],
    # 0x30 '0'
    [0x7c,0xc6,0xce,0xde,0xf6,0xe6,0x7c,0x00],
    # 0x31 '1'
    [0x18,0x38,0x18,0x18,0x18,0x18,0x7e,0x00],
    # 0x32 '2'
    [0x7c,0xc6,0x06,0x1c,0x70,0xc0,0xfe,0x00],
    # 0x33 '3'
    [0x7c,0xc6,0x06,0x3c,0x06,0xc6,0x7c,0x00],
    # 0x34 '4'
    [0x1c,0x3c,0x6c,0xcc,0xfe,0x0c,0x1e,0x00],
    # 0x35 '5'
    [0xfe,0xc0,0xfc,0x06,0x06,0xc6,0x7c,0x00],
    # 0x36 '6'
    [0x3c,0x60,0xc0,0xfc,0xc6,0xc6,0x7c,0x00],
    # 0x37 '7'
    [0xfe,0xc6,0x0c,0x18,0x30,0x30,0x30,0x00],
    # 0x38 '8'
    [0x7c,0xc6,0xc6,0x7c,0xc6,0xc6,0x7c,0x00],
    # 0x39 '9'
    [0x7c,0xc6,0xc6,0x7e,0x06,0x0c,0x78,0x00],
    # 0x3A ':'
    [0x00,0x18,0x18,0x00,0x00,0x18,0x18,0x00],
    # 0x3B ';'
    [0x00,0x18,0x18,0x00,0x00,0x18,0x18,0x30],
    # 0x3C '<'
    [0x0e,0x1c,0x38,0x70,0x38,0x1c,0x0e,0x00],
    # 0x3D '='
    [0x00,0x00,0x7e,0x00,0x00,0x7e,0x00,0x00],
    # 0x3E '>'
    [0x70,0x38,0x1c,0x0e,0x1c,0x38,0x70,0x00],
    # 0x3F '?'
    [0x7c,0xc6,0x0e,0x1c,0x18,0x00,0x18,0x00],
    # 0x40 '@'
    [0x7c,0xc6,0xde,0xde,0xde,0xc0,0x78,0x00],
    # 0x41 'A'
    [0x38,0x6c,0xc6,0xc6,0xfe,0xc6,0xc6,0x00],
    # 0x42 'B'
    [0xfc,0x66,0x66,0x7c,0x66,0x66,0xfc,0x00],
    # 0x43 'C'
    [0x3c,0x66,0xc0,0xc0,0xc0,0x66,0x3c,0x00],
    # 0x44 'D'
    [0xf8,0x6c,0x66,0x66,0x66,0x6c,0xf8,0x00],
    # 0x45 'E'
    [0xfe,0x62,0x68,0x78,0x68,0x62,0xfe,0x00],
    # 0x46 'F'
    [0xfe,0x62,0x68,0x78,0x68,0x60,0xf0,0x00],
    # 0x47 'G'
    [0x3c,0x66,0xc0,0xc0,0xce,0x66,0x3e,0x00],
    # 0x48 'H'
    [0xc6,0xc6,0xc6,0xfe,0xc6,0xc6,0xc6,0x00],
    # 0x49 'I'
    [0x3c,0x18,0x18,0x18,0x18,0x18,0x3c,0x00],
    # 0x4A 'J'
    [0x1e,0x0c,0x0c,0x0c,0xcc,0xcc,0x78,0x00],
    # 0x4B 'K'
    [0xe6,0x66,0x6c,0x78,0x6c,0x66,0xe6,0x00],
    # 0x4C 'L'
    [0xf0,0x60,0x60,0x60,0x62,0x66,0xfe,0x00],
    # 0x4D 'M'
    [0xc6,0xee,0xfe,0xfe,0xd6,0xc6,0xc6,0x00],
    # 0x4E 'N'
    [0xc6,0xe6,0xf6,0xde,0xce,0xc6,0xc6,0x00],
    # 0x4F 'O'
    [0x7c,0xc6,0xc6,0xc6,0xc6,0xc6,0x7c,0x00],
    # 0x50 'P'
    [0xfc,0x66,0x66,0x7c,0x60,0x60,0xf0,0x00],
    # 0x51 'Q'
    [0x7c,0xc6,0xc6,0xc6,0xd6,0xcc,0x76,0x00],
    # 0x52 'R'
    [0xfc,0x66,0x66,0x7c,0x6c,0x66,0xe6,0x00],
    # 0x53 'S'
    [0x7c,0xc6,0x60,0x38,0x0c,0xc6,0x7c,0x00],
    # 0x54 'T'
    [0x7e,0x7e,0x5a,0x18,0x18,0x18,0x3c,0x00],
    # 0x55 'U'
    [0xc6,0xc6,0xc6,0xc6,0xc6,0xc6,0x7c,0x00],
    # 0x56 'V'
    [0xc6,0xc6,0xc6,0xc6,0xc6,0x6c,0x38,0x00],
    # 0x57 'W'
    [0xc6,0xc6,0xc6,0xd6,0xfe,0xee,0xc6,0x00],
    # 0x58 'X'
    [0xc6,0xc6,0x6c,0x38,0x38,0x6c,0xc6,0x00],
    # 0x59 'Y'
    [0x66,0x66,0x66,0x3c,0x18,0x18,0x3c,0x00],
    # 0x5A 'Z'
    [0xfe,0xc6,0x8c,0x18,0x32,0x66,0xfe,0x00],
    # 0x5B '['
    [0x3c,0x30,0x30,0x30,0x30,0x30,0x3c,0x00],
    # 0x5C '\'
    [0xc0,0x60,0x30,0x18,0x0c,0x06,0x02,0x00],
    # 0x5D ']'
    [0x3c,0x0c,0x0c,0x0c,0x0c,0x0c,0x3c,0x00],
    # 0x5E '^'
    [0x10,0x38,0x6c,0xc6,0x00,0x00,0x00,0x00],
    # 0x5F '_'
    [0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xfe],
    # 0x60 '`'
    [0x30,0x18,0x0c,0x00,0x00,0x00,0x00,0x00],
    # 0x61 'a'
    [0x00,0x00,0x78,0x0c,0x7c,0xcc,0x76,0x00],
    # 0x62 'b'
    [0xe0,0x60,0x7c,0x66,0x66,0x66,0xdc,0x00],
    # 0x63 'c'
    [0x00,0x00,0x7c,0xc6,0xc0,0xc6,0x7c,0x00],
    # 0x64 'd'
    [0x1c,0x0c,0x7c,0xcc,0xcc,0xcc,0x76,0x00],
    # 0x65 'e'
    [0x00,0x00,0x7c,0xc6,0xfe,0xc0,0x7c,0x00],
    # 0x66 'f'
    [0x1c,0x36,0x30,0x78,0x30,0x30,0x78,0x00],
    # 0x67 'g'
    [0x00,0x00,0x76,0xcc,0xcc,0x7c,0x0c,0x78],
    # 0x68 'h'
    [0xe0,0x60,0x6c,0x76,0x66,0x66,0xe6,0x00],
    # 0x69 'i'
    [0x18,0x00,0x38,0x18,0x18,0x18,0x3c,0x00],
    # 0x6A 'j'
    [0x06,0x00,0x06,0x06,0x06,0x66,0x66,0x3c],
    # 0x6B 'k'
    [0xe0,0x60,0x66,0x6c,0x78,0x6c,0xe6,0x00],
    # 0x6C 'l'
    [0x38,0x18,0x18,0x18,0x18,0x18,0x3c,0x00],
    # 0x6D 'm'
    [0x00,0x00,0xec,0xfe,0xd6,0xd6,0xd6,0x00],
    # 0x6E 'n'
    [0x00,0x00,0xdc,0x66,0x66,0x66,0x66,0x00],
    # 0x6F 'o'
    [0x00,0x00,0x7c,0xc6,0xc6,0xc6,0x7c,0x00],
    # 0x70 'p'
    [0x00,0x00,0xdc,0x66,0x66,0x7c,0x60,0xf0],
    # 0x71 'q'
    [0x00,0x00,0x76,0xcc,0xcc,0x7c,0x0c,0x1e],
    # 0x72 'r'
    [0x00,0x00,0xdc,0x76,0x60,0x60,0xf0,0x00],
    # 0x73 's'
    [0x00,0x00,0x7c,0xc0,0x7c,0x06,0xfc,0x00],
    # 0x74 't'
    [0x30,0x30,0x7c,0x30,0x30,0x36,0x1c,0x00],
    # 0x75 'u'
    [0x00,0x00,0xcc,0xcc,0xcc,0xcc,0x76,0x00],
    # 0x76 'v'
    [0x00,0x00,0xc6,0xc6,0xc6,0x6c,0x38,0x00],
    # 0x77 'w'
    [0x00,0x00,0xc6,0xd6,0xd6,0xfe,0x6c,0x00],
    # 0x78 'x'
    [0x00,0x00,0xc6,0x6c,0x38,0x6c,0xc6,0x00],
    # 0x79 'y'
    [0x00,0x00,0xc6,0xc6,0xce,0x76,0x06,0x7c],
    # 0x7A 'z'
    [0x00,0x00,0xfe,0x8c,0x18,0x32,0xfe,0x00],
]

# Pad to 128 entries by filling with blanks for rest of ASCII.
while len(FONT8X8_BASIC) < 128:
    FONT8X8_BASIC.append([0x00] * 8)
# fmt: on


@dataclass
class Host:
    mac: str
    name: str
    connected: bool = False
    live: bool = False


@dataclass
class ScanDevice:
    mac: str
    name: str


class Framebuffer:
    def __init__(self, path: str = FB_DEV):
        self.path = path
        self.fd: Optional[int] = None
        self.page_len = FB_STRIDE * FB_HEIGHT
        self.buf = bytearray(self.page_len)
        # NOTE: On this kernel, large writes / dd-to-fb0 can crash, and panning
        # can be unstable. We keep pan pinned to page 0 and only write page 0
        # using small chunks.
        self._pan_path = Path("/sys/class/graphics/fb0/pan")
        self._ver_update_pan_path = Path("/sys/class/graphics/fb0/ver_update_pan")
        self._osd_plane_alpha_path = Path("/sys/class/graphics/fb0/osd_plane_alpha")
        self._window_axis_path = Path("/sys/class/graphics/fb0/window_axis")

    def _set_pan_zero(self) -> None:
        if not self._pan_path.exists():
            return
        try:
            self._pan_path.write_text("0,0")
        except Exception:
            pass

    def open(self) -> bool:
        try:
            self.fd = os.open(self.path, os.O_RDWR)
            self._set_pan_zero()
            # Switch to 32bpp BGRA.  24bpp leaves alpha undefined → pixel noise.
            try:
                import array as _arr
                buf = _arr.array("B", bytes(160))
                fcntl.ioctl(self.fd, FBIOGET_VSCREENINFO, buf)
                struct.pack_into("I", buf, 24, 32)        # bpp=32
                struct.pack_into("III", buf, 32, 16, 8, 0)  # red
                struct.pack_into("III", buf, 44, 8, 8, 0)   # green
                struct.pack_into("III", buf, 56, 0, 8, 0)   # blue
                struct.pack_into("III", buf, 68, 24, 8, 0)  # alpha
                fcntl.ioctl(self.fd, FBIOPUT_VSCREENINFO, buf)
            except Exception:
                pass
            try:
                # Fully opaque (0x300 = global alpha mode, value 256).
                if self._osd_plane_alpha_path.exists():
                    self._osd_plane_alpha_path.write_text("0x300")
            except Exception:
                pass
            try:
                if self._window_axis_path.exists():
                    self._window_axis_path.write_text("0 0 479 799")
            except Exception:
                pass
            # Disable other OSD planes so they don't blend garbage over fb0.
            for other in ("fb1", "fb2"):
                p = Path(f"/sys/class/graphics/{other}/osd_plane_alpha")
                try:
                    if p.exists():
                        p.write_text("0x000")
                except Exception:
                    pass
            # Enable the OSD plane (starts disabled after boot).
            try:
                fcntl.ioctl(self.fd, FBIOBLANK, 0)
            except Exception:
                pass
            return True
        except OSError as e:
            LOG.error("Failed to open framebuffer %s: %s", self.path, e)
            self.fd = None
            return False

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def clear(self, color: tuple) -> None:
        b, g, r, a = color[0], color[1], color[2], color[3] if len(color) > 3 else 0xFF
        pixel = bytes([b, g, r, a])
        row = pixel * FB_WIDTH
        for y in range(FB_HEIGHT):
            start = y * FB_STRIDE
            self.buf[start : start + FB_STRIDE] = row

    def blit_logo(self) -> bool:
        for p in LOGO_FB_PATHS:
            try:
                data = Path(p).read_bytes()
            except OSError:
                continue
            if len(data) != self.page_len:
                continue
            self.buf[:] = data
            return True
        return False

    def fill_rect(self, x: int, y: int, w: int, h: int, color: tuple) -> None:
        x = max(0, min(FB_WIDTH, x))
        y = max(0, min(FB_HEIGHT, y))
        w = max(0, min(FB_WIDTH - x, w))
        h = max(0, min(FB_HEIGHT - y, h))
        if w == 0 or h == 0:
            return

        b, g, r, a = color[0], color[1], color[2], color[3] if len(color) > 3 else 0xFF
        pixel = bytes([b, g, r, a])
        row = pixel * w
        for yy in range(y, y + h):
            start = yy * FB_STRIDE + x * FB_BPP
            self.buf[start : start + w * FB_BPP] = row

    def draw_char(self, x: int, y: int, ch: str, color: tuple, scale: int = 1) -> None:
        if not ch:
            return
        ch0 = ch[0]
        if ch0 == " ":
            return
        code = ord(ch0) & 0x7F
        if code >= len(FONT8X8_BASIC) or FONT8X8_BASIC[code] == [0x00] * 8:
            code = 0x3F  # '?'
        glyph = FONT8X8_BASIC[code]
        for row_idx in range(8):
            bits = glyph[row_idx]
            for col in range(8):
                if bits & (1 << col):
                    px = x + (7 - col) * scale
                    py = y + row_idx * scale
                    self.fill_rect(px, py, scale, scale, color)

    def draw_text(self, x: int, y: int, text: str, color: tuple, scale: int = 1, spacing: int = 1) -> None:
        cx = x
        for ch in text:
            if ch == "\n":
                y += (8 * scale) + spacing
                cx = x
                continue
            self.draw_char(cx, y, ch, color, scale=scale)
            cx += (8 * scale) + spacing

    def present(self) -> None:
        if self.fd is None:
            return
        view = memoryview(self.buf)

        # Seek to page 0 (retry on EINTR).
        while True:
            try:
                os.lseek(self.fd, 0, os.SEEK_SET)
                break
            except OSError as e:
                if e.errno == errno.EINTR:
                    continue
                LOG.error("Framebuffer seek failed: %s", e)
                return

        off = 0
        while off < self.page_len:
            chunk_len = min(4 * 1024, self.page_len - off)
            try:
                n = os.write(self.fd, view[off : off + chunk_len])
            except OSError as e:
                if e.errno == errno.EINTR:
                    continue
                LOG.error("Framebuffer write failed: %s", e)
                return
            if n <= 0:
                return
            off += n

        try:
            # Amlogic OSD driver: force scanout refresh.
            if self._ver_update_pan_path.exists():
                self._ver_update_pan_path.write_text("1")
        except Exception:
            pass


class Touch:
    def __init__(self, path: str = TOUCH_DEV):
        self.path = path
        self.fd: Optional[int] = None
        self.x = 0
        self.y = 0
        self.down = False

    def open(self) -> bool:
        try:
            self.fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
            return True
        except OSError as e:
            LOG.error("Failed to open touch device %s: %s", self.path, e)
            self.fd = None
            return False

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def fileno(self) -> int:
        if self.fd is None:
            raise RuntimeError("touch not open")
        return self.fd

    def read_events(self) -> list[tuple[str, int, int]]:
        if self.fd is None:
            return []
        out = []
        while True:
            try:
                data = os.read(self.fd, EVENT_SIZE)
            except BlockingIOError:
                break
            except OSError as e:
                if e.errno == errno.EINTR:
                    continue
                LOG.error("Touch read error: %s", e)
                break
            if not data or len(data) < EVENT_SIZE:
                break
            _sec, _usec, etype, code, value = struct.unpack(EVENT_FORMAT, data)
            out.append(("raw", etype, code))

            if etype == EV_ABS:
                if code == ABS_MT_POSITION_X:
                    self.x = max(0, min(FB_WIDTH - 1, int(value)))
                elif code == ABS_MT_POSITION_Y:
                    self.y = max(0, min(FB_HEIGHT - 1, int(value)))
                elif code == ABS_MT_TRACKING_ID:
                    if value == TRACKING_ID_UP:
                        # finger lifted
                        self.down = False
                        out.append(("up", self.x, self.y))
                    else:
                        self.down = True
                        out.append(("down", self.x, self.y))
            elif etype == EV_KEY:
                # Some panels also emit BTN_TOUCH events; we can ignore.
                pass
        return out


def run_cmd(args: list[str], timeout: float = 5.0) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return 1, str(e)


def get_adapter_addr() -> Optional[str]:
    rc, out = run_cmd(["hciconfig", "hci0"], timeout=2.0)
    if rc != 0:
        return None
    m = re.search(r"BD Address: ([0-9A-Fa-f:]{17})", out)
    return m.group(1) if m else None


def paired_hosts() -> list[Host]:
    adapter = get_adapter_addr()
    if not adapter:
        return []
    base = Path("/var/lib/bluetooth") / adapter
    if not base.is_dir():
        return []
    hosts: list[Host] = []
    for info in base.glob("*/info"):
        try:
            txt = info.read_text(errors="ignore")
        except OSError:
            continue
        if "[LinkKey]" not in txt:
            continue
        mac = info.parent.name
        name = mac
        for line in txt.splitlines():
            if line.startswith("Name="):
                name = line.split("=", 1)[1].strip() or mac
                break
        hosts.append(Host(mac=mac, name=name, connected=False))
    # stable ordering
    hosts.sort(key=lambda h: (h.name.lower(), h.mac))
    return hosts


def build_host_state(hosts: list[Host], connected_macs: set[str], live_macs: set[str]) -> list[Host]:
    stateful_hosts: list[Host] = []
    for host in hosts:
        connected = host.mac in connected_macs
        live = connected or (host.mac in live_macs)
        stateful_hosts.append(Host(mac=host.mac, name=host.name, connected=connected, live=live))
    stateful_hosts.sort(key=lambda h: (not h.connected, not h.live, h.name.lower(), h.mac))
    return stateful_hosts


def dev_path(mac: str) -> str:
    return "/org/bluez/hci0/dev_" + mac.replace(":", "_")


def dbus_get_connected(mac: str) -> bool:
    path = dev_path(mac)
    rc, out = run_cmd(
        [
            "dbus-send",
            "--system",
            "--print-reply",
            "--dest=org.bluez",
            path,
            "org.freedesktop.DBus.Properties.Get",
            "string:org.bluez.Device1",
            "string:Connected",
        ],
        timeout=3.0,
    )
    return rc == 0 and "boolean true" in out


def dbus_connect(mac: str) -> bool:
    path = dev_path(mac)
    rc, _out = run_cmd(
        ["dbus-send", "--system", "--print-reply", "--dest=org.bluez", path, "org.bluez.Device1.Connect"],
        timeout=10.0,
    )
    return rc == 0


def dbus_remove(mac: str) -> bool:
    path = dev_path(mac)
    rc, _out = run_cmd(
        [
            "dbus-send",
            "--system",
            "--print-reply",
            "--dest=org.bluez",
            "/org/bluez/hci0",
            "org.bluez.Adapter1.RemoveDevice",
            f"object_path:{path}",
        ],
        timeout=5.0,
    )
    return rc == 0


def scan_devices(timeout_s: int = 8) -> list[ScanDevice]:
    # bluetoothctl prints lines like:
    #   [NEW] Device AA:BB:CC:DD:EE:FF Name
    rc, out = run_cmd(["bluetoothctl", f"--timeout={timeout_s}", "scan", "on"], timeout=timeout_s + 2)
    if rc != 0 and not out:
        return []
    found: dict[str, str] = {}
    for line in out.splitlines():
        m = re.search(r"Device ([0-9A-Fa-f:]{17}) (.+)$", line)
        if not m:
            continue
        mac = m.group(1).upper()
        name = m.group(2).strip()
        if mac and name:
            found[mac] = name
    devices = [ScanDevice(mac=m, name=n) for m, n in found.items()]
    devices.sort(key=lambda d: (d.name.lower(), d.mac))
    return devices


def probe_live_hosts(target_macs: set[str], timeout_s: int = HOME_LIVE_SCAN_TIMEOUT_S) -> set[str]:
    if not target_macs:
        return set()
    rc, out = run_cmd(["bluetoothctl", f"--timeout={timeout_s}", "scan", "on"], timeout=timeout_s + 2)
    if rc != 0 and not out:
        return set()
    found: set[str] = set()
    for line in out.splitlines():
        m = re.search(r"Device ([0-9A-Fa-f:]{17}) (.+)$", line)
        if not m:
            continue
        mac = m.group(1).upper()
        if mac in target_macs:
            found.add(mac)
    return found


class MenuUI:
    def __init__(self):
        self.visible = False
        self.fb = Framebuffer()
        self.touch = Touch()

        self.hosts: list[Host] = []
        self.scan_results: list[ScanDevice] = []
        self.scanning = False
        self.last_error: Optional[str] = None
        self._scan_thread: Optional[threading.Thread] = None
        self._live_probe_thread: Optional[threading.Thread] = None
        self._live_macs: set[str] = set()
        self._last_live_probe = 0.0

        self.confirm_delete: Optional[Host] = None
        self.home_status: Optional[str] = None
        self._home_status_until = 0.0

    def _set_status(self, msg: Optional[str], duration_s: float = 2.0) -> None:
        self.last_error = msg
        self.home_status = msg
        self._home_status_until = time.time() + duration_s if msg else 0.0

    def _clear_expired_status(self) -> None:
        if self._home_status_until and time.time() >= self._home_status_until:
            self.last_error = None
            self.home_status = None
            self._home_status_until = 0.0

    def refresh_hosts(self) -> None:
        base_hosts = paired_hosts()
        connected_macs: set[str] = set()
        for host in base_hosts:
            try:
                if dbus_get_connected(host.mac):
                    connected_macs.add(host.mac)
            except Exception:
                pass
        self.hosts = build_host_state(base_hosts, connected_macs, self._live_macs)

    def _start_live_probe(self) -> None:
        if self.scanning:
            return
        if self._live_probe_thread and self._live_probe_thread.is_alive():
            return
        now = time.time()
        if now - self._last_live_probe < HOME_LIVE_SCAN_INTERVAL_S:
            return
        target_macs = {host.mac for host in self.hosts} or {host.mac for host in paired_hosts()}
        if not target_macs:
            self._live_macs = set()
            self.refresh_hosts()
            return
        self._last_live_probe = now

        def worker() -> None:
            try:
                live_macs = probe_live_hosts(target_macs)
            except Exception:
                live_macs = set()
            self._live_macs = live_macs
            self.refresh_hosts()
            self.draw_current()

        self._live_probe_thread = threading.Thread(target=worker, daemon=True)
        self._live_probe_thread.start()

    def draw_current(self) -> None:
        if self.visible:
            self.draw()
        else:
            self.draw_idle()

    def _text_width(self, text: str, scale: int = 1, spacing: int = 1) -> int:
        if not text:
            return 0
        return len(text) * (8 * scale + spacing) - spacing

    def _draw_logo(self, x: int, y: int, scale: int = 2, spacing: int = 1) -> None:
        cx = x
        for idx, ch in enumerate("spotifone"):
            color = TEXT if idx < 6 else PURPLE
            self.fb.draw_char(cx, y, ch, color, scale=scale)
            cx += (8 * scale) + spacing

    def _draw_centered_logo(self, y: int, scale: int = 2, spacing: int = 1) -> None:
        word = "spotifone"
        x = max(0, (FB_WIDTH - self._text_width(word, scale=scale, spacing=spacing)) // 2)
        self._draw_logo(x, y, scale=scale, spacing=spacing)

    def _draw_centered_text(self, y: int, text: str, color: tuple, scale: int = 1, spacing: int = 1) -> None:
        width = self._text_width(text, scale=scale, spacing=spacing)
        x = max(0, (FB_WIDTH - width) // 2)
        self.fb.draw_text(x, y, text, color, scale=scale, spacing=spacing)

    def _home_layout(self) -> list[tuple[Host, int, int, int, int]]:
        top = 160
        bottom = FB_HEIGHT - 56
        side_pad = 24
        col_gap = 18
        row_gap = 18
        cols = HOME_GRID_COLUMNS
        rows = max(1, (len(self.hosts) + cols - 1) // cols)
        usable_h = max(0, bottom - top)
        tile_area_w = FB_WIDTH - side_pad * 2 - HOME_LEGEND_WIDTH
        tile_w = (tile_area_w - col_gap * (cols - 1)) // cols
        tile_h = min(150, max(96, (usable_h - row_gap * max(0, rows - 1)) // rows))
        layout: list[tuple[Host, int, int, int, int]] = []
        for idx, host in enumerate(self.hosts):
            row = idx // cols
            col = idx % cols
            x = side_pad + col * (tile_w + col_gap)
            y = top + row * (tile_h + row_gap)
            layout.append((host, x, y, tile_w, tile_h))
        return layout

    def _home_legend_layout(self) -> list[tuple[str, int, int, int, int]]:
        x0 = FB_WIDTH - 58
        return [
            ("Menu", x0, 120, 50, 20),
            ("Left", x0, 264, 50, 18),
            ("Enter", x0 - 4, 292, 58, 24),
            ("Right", x0, 326, 50, 18),
            ("Del", x0, 388, 50, 20),
        ]

    def _draw_home_icon(self, x: int, y: int, w: int, live: bool, connected: bool) -> None:
        frame = TEXT if live else TEXT_DIM
        inner = SURFACE if live else BG
        accent = PURPLE if connected else (GREEN if live else TEXT_DIM)
        screen_w = min(76, w - 48)
        screen_h = 42
        screen_x = x + (w - screen_w) // 2
        screen_y = y
        self.fb.fill_rect(screen_x, screen_y, screen_w, screen_h, frame)
        self.fb.fill_rect(screen_x + 3, screen_y + 3, screen_w - 6, screen_h - 6, inner)
        self.fb.fill_rect(screen_x + screen_w // 2 - 3, screen_y + screen_h, 6, 12, frame)
        self.fb.fill_rect(screen_x + screen_w // 2 - 18, screen_y + screen_h + 12, 36, 4, frame)
        self.fb.fill_rect(screen_x + 8, screen_y + 8, screen_w - 16, 4, accent)
        self.fb.fill_rect(screen_x + 8, screen_y + 16, screen_w - 26, 4, frame)
        self.fb.fill_rect(screen_x + 8, screen_y + 24, screen_w - 34, 4, frame)

    def _home_tile_style(self, host: Host) -> tuple[tuple, tuple, tuple]:
        border_color = PURPLE if host.connected else BORDER
        fill_color = SURFACE if host.live else BORDER
        name_color = TEXT if host.live else TEXT_DIM
        return border_color, fill_color, name_color

    def _draw_home_legend(self) -> None:
        rail_x = FB_WIDTH - 20
        self.fb.fill_rect(rail_x, 110, 2, 314, BORDER)
        for label, x, y, w, h in self._home_legend_layout():
            border = PURPLE if label in ("Menu", "Enter") else BORDER
            fill = SURFACE
            text = TEXT if label in ("Menu", "Enter") else TEXT_DIM
            self.fb.fill_rect(x, y, w, h, border)
            self.fb.fill_rect(x + 1, y + 1, w - 2, h - 2, fill)
            if label in ("Menu", "Enter"):
                self.fb.fill_rect(rail_x - 2, y, 4, h, PURPLE)
            text_x = x + max(0, (w - self._text_width(label, scale=1, spacing=1)) // 2)
            text_y = y + max(0, (h - 8) // 2)
            self.fb.draw_text(text_x, text_y, label, text, scale=1)

    def toggle(self) -> None:
        self.visible = not self.visible
        self.last_error = None
        self.confirm_delete = None
        LOG.info("toggle visible=%s", self.visible)
        if self.visible:
            self.refresh_hosts()
            self.draw()
        else:
            self.draw_idle()

    def draw_idle(self) -> None:
        self._clear_expired_status()
        self.fb.clear(BG)
        self._draw_centered_logo(22, scale=2, spacing=1)
        self._draw_centered_text(62, "Bluetooth mic + keyboard. no setup, just talk.", TEXT, scale=1, spacing=1)
        self.fb.fill_rect(20, 112, FB_WIDTH - 40, 4, PURPLE)
        self._draw_centered_text(128, "hosts", TEXT_DIM, scale=1, spacing=1)

        if not self.hosts:
            self._draw_centered_text(240, "no remembered hosts yet", TEXT_DIM, scale=1)

        for host, x, y, tile_w, tile_h in self._home_layout():
            border_color, fill_color, name_color = self._home_tile_style(host)
            self.fb.fill_rect(x, y, tile_w, tile_h, border_color)
            inset = 3 if host.connected else 1
            self.fb.fill_rect(x + inset, y + inset, tile_w - inset * 2, tile_h - inset * 2, fill_color)
            if host.connected:
                self.fb.fill_rect(x + tile_w - 24, y + 10, 12, 12, PURPLE)
            elif host.live:
                self.fb.fill_rect(x + tile_w - 20, y + 10, 8, 8, GREEN)
            self._draw_home_icon(x, y + 18, tile_w, host.live, host.connected)
            name = (host.name[:16] + "...") if len(host.name) > 19 else host.name
            name_x = x + max(0, (tile_w - self._text_width(name, scale=1, spacing=1)) // 2)
            self.fb.draw_text(name_x, y + tile_h - 28, name, name_color, scale=1)

        self._draw_home_legend()

        if self.home_status:
            self._draw_centered_text(FB_HEIGHT - 28, self.home_status[:56], TEXT_DIM, scale=1)
        self.fb.present()

    def draw(self) -> None:
        # Base background
        self.fb.clear(BG)

        # Header bar
        self.fb.fill_rect(0, 0, FB_WIDTH, 72, SURFACE)
        self.fb.fill_rect(0, 0, FB_WIDTH, 6, PURPLE)
        self._draw_logo(20, 18, scale=2, spacing=1)
        self.fb.draw_text(20, 52, "no setup. just talk.", TEXT_DIM, scale=1, spacing=1)

        # Close button (X)
        self.fb.fill_rect(FB_WIDTH - 64, 16, 48, 40, BORDER)
        self.fb.fill_rect(FB_WIDTH - 63, 17, 46, 38, SURFACE)
        self.fb.draw_text(FB_WIDTH - 54, 28, "X", TEXT, scale=2, spacing=1)

        y = 88
        self.fb.draw_text(20, y, "01 hosts", TEXT_DIM, scale=1, spacing=1)
        y += 18

        row_h = 58
        # Leave vertical space for scan results + about footer.
        max_rows = 5
        shown = self.hosts[:max_rows]
        for idx, h in enumerate(shown):
            ry = y + idx * (row_h + 10)
            self.fb.fill_rect(16, ry, FB_WIDTH - 32, row_h, BORDER)
            self.fb.fill_rect(17, ry + 1, FB_WIDTH - 34, row_h - 2, SURFACE)
            status_color = GREEN if h.connected else TEXT_DIM
            self.fb.fill_rect(24, ry + 18, 10, 10, status_color)
            name = (h.name[:18] + "...") if len(h.name) > 21 else h.name
            self.fb.draw_text(44, ry + 14, name, TEXT, scale=1)
            mac = h.mac
            self.fb.draw_text(44, ry + 32, mac, TEXT_DIM, scale=1)
            # Delete area
            self.fb.fill_rect(FB_WIDTH - 72, ry + 10, 40, 38, BORDER)
            self.fb.fill_rect(FB_WIDTH - 71, ry + 11, 38, 36, SURFACE)
            self.fb.draw_text(FB_WIDTH - 62, ry + 20, "del", RED, scale=1)

        y2 = y + max_rows * (row_h + 10) + 10
        self.fb.draw_text(20, y2 - 18, "02 scan", TEXT_DIM, scale=1, spacing=1)
        # Scan button
        self.fb.fill_rect(16, y2, FB_WIDTH - 32, 56, BORDER)
        self.fb.fill_rect(17, y2 + 1, FB_WIDTH - 34, 54, SURFACE)
        self.fb.fill_rect(16, y2, FB_WIDTH - 32, 4, PURPLE)
        label = "scanning..." if self.scanning else "scan for host"
        self.fb.draw_text(36, y2 + 20, label, PURPLE if self.scanning else TEXT, scale=2, spacing=1)

        y3 = y2 + 72
        if self.scan_results:
            self.fb.draw_text(20, y3, "results", TEXT_DIM, scale=1)
            y3 += 18
            # Keep results above the ABOUT footer.
            for i, d in enumerate(self.scan_results[:3]):
                ry = y3 + i * 44
                self.fb.fill_rect(16, ry, FB_WIDTH - 32, 38, BORDER)
                self.fb.fill_rect(17, ry + 1, FB_WIDTH - 34, 36, SURFACE)
                name = (d.name[:20] + "...") if len(d.name) > 23 else d.name
                self.fb.draw_text(24, ry + 12, name, TEXT, scale=1)
                self.fb.draw_text(240, ry + 12, d.mac, TEXT_DIM, scale=1)

        # About footer
        self.fb.draw_text(20, FB_HEIGHT - 88, "03 about", TEXT_DIM, scale=1)
        self.fb.draw_text(20, FB_HEIGHT - 68, f"version: {read_version()}", TEXT_DIM, scale=1)
        self.fb.draw_text(20, FB_HEIGHT - 48, "https://seanslab.org", TEXT_DIM, scale=1)

        # Error banner
        if self.last_error:
            self.fb.fill_rect(0, FB_HEIGHT - 28, FB_WIDTH, 28, (0x20, 0x20, 0x80, 0xFF))
            msg = self.last_error[:54]
            self.fb.draw_text(10, FB_HEIGHT - 22, msg, TEXT, scale=1)

        # Confirm delete overlay
        if self.confirm_delete:
            self.fb.fill_rect(40, 260, FB_WIDTH - 80, 220, BORDER)
            self.fb.fill_rect(41, 261, FB_WIDTH - 82, 218, SURFACE)
            self.fb.fill_rect(40, 260, FB_WIDTH - 80, 6, RED)
            self.fb.draw_text(60, 290, "delete host?", TEXT, scale=2)
            self.fb.draw_text(60, 320, self.confirm_delete.name[:22], TEXT_DIM, scale=1)
            # Buttons
            self.fb.fill_rect(70, 400, 140, 56, GREEN)
            self.fb.draw_text(110, 420, "yes", TEXT, scale=2)
            self.fb.fill_rect(270, 400, 140, 56, RED)
            self.fb.draw_text(310, 420, "no", TEXT, scale=2)

        self.fb.present()

    def _start_scan_thread(self) -> None:
        if self.scanning:
            return

        def worker():
            try:
                self.scan_results = scan_devices(timeout_s=8)
            except Exception as e:
                self.last_error = f"scan failed: {e}"
                self.scan_results = []
            finally:
                self.scanning = False
                if self.visible:
                    self.draw()

        self.scanning = True
        self.scan_results = []
        self._scan_thread = threading.Thread(target=worker, daemon=True)
        self._scan_thread.start()

    def _connect_via_bluetoothctl(self, mac: str) -> bool:
        # Try connect, then pair+trust+connect. This may fail if the host isn't in pairing mode.
        _rc, out = run_cmd(["bluetoothctl", "connect", mac], timeout=12.0)
        if "Connection successful" in out:
            return True

        _rc, out = run_cmd(["bluetoothctl", "--timeout=20", "pair", mac], timeout=22.0)
        if "Pairing successful" in out or "pairing successful" in out.lower():
            run_cmd(["bluetoothctl", "trust", mac], timeout=5.0)
            _rc, out2 = run_cmd(["bluetoothctl", "connect", mac], timeout=12.0)
            if "Connection successful" in out2:
                return True

        return False

    def _attempt_connect_device(self, mac: str) -> None:
        self._set_status("connecting...", duration_s=8.0)
        self.draw_current()

        ok = False
        try:
            ok = dbus_connect(mac)
        except Exception:
            ok = False

        if not ok:
            ok = self._connect_via_bluetoothctl(mac)

        self._set_status("connected" if ok else "connect failed (pairing mode?)", duration_s=2.5)
        time.sleep(0.3)
        self.refresh_hosts()
        self.draw_current()

    def on_tap(self, x: int, y: int) -> None:
        if not self.visible:
            for host, tile_x, tile_y, tile_w, tile_h in self._home_layout():
                if tile_x <= x <= tile_x + tile_w and tile_y <= y <= tile_y + tile_h:
                    self._attempt_connect_device(host.mac)
                    return
            return

        # Confirm delete overlay has priority
        if self.confirm_delete:
            # YES
            if 70 <= x <= 210 and 400 <= y <= 456:
                host = self.confirm_delete
                self.confirm_delete = None
                ok = dbus_remove(host.mac)
                if not ok:
                    self.last_error = "remove failed"
                self.refresh_hosts()
                self.draw()
                return
            # NO
            if 270 <= x <= 410 and 400 <= y <= 456:
                self.confirm_delete = None
                self.draw()
                return
            return

        # Close button
        if x >= FB_WIDTH - 64 and y <= 72:
            self.toggle()
            return

        # Host rows
        host_section_top = 106
        row_h = 58
        row_gap = 10
        max_rows = 5
        for idx, h in enumerate(self.hosts[:max_rows]):
            ry = host_section_top + idx * (row_h + row_gap)
            if 16 <= x <= FB_WIDTH - 16 and ry <= y <= ry + row_h:
                # Delete region
                if x >= FB_WIDTH - 72:
                    self.confirm_delete = h
                    self.draw()
                    return
                # Connect
                self._attempt_connect_device(h.mac)
                return

        # Scan button
        scan_y = host_section_top + max_rows * (row_h + row_gap) + 10
        if 16 <= x <= FB_WIDTH - 16 and scan_y <= y <= scan_y + 56:
            self._start_scan_thread()
            self.draw()
            return

        # Scan result rows (tap-to-connect)
        if self.scan_results:
            results_top = scan_y + 72 + 18  # matches draw(): y3=y2+72, then y3+=18
            for i, d in enumerate(self.scan_results[:3]):
                ry = results_top + i * 44
                if 16 <= x <= FB_WIDTH - 16 and ry <= y <= ry + 38:
                    self._attempt_connect_device(d.mac)
                    return


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")

    # File log (preferred; bt_init may start us without stdout/stderr capture).
    try:
        fh = logging.FileHandler(MENU_LOG_PATH)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception:
        pass

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)


def read_version() -> str:
    try:
        s = Path(VERSION_PATH).read_text(errors="ignore").strip()
        if s:
            return s.splitlines()[0][:24]
    except OSError:
        pass
    return "DEV"


def main() -> int:
    setup_logging()
    LOG.info("menu_ui starting")

    # Menu socket
    menu_sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        os.unlink(MENU_SOCK_PATH)
    except FileNotFoundError:
        pass
    menu_sock.bind(MENU_SOCK_PATH)
    os.chmod(MENU_SOCK_PATH, 0o666)

    ui = MenuUI()
    ui.fb.open()
    ui.touch.open()
    ui.refresh_hosts()
    ui.draw_idle()

    last_refresh = 0.0
    while True:
        rlist = [menu_sock]
        if ui.touch.fd is not None:
            rlist.append(ui.touch.fileno())

        timeout = 0.2
        try:
            ready, _, _ = select.select(rlist, [], [], timeout)
        except InterruptedError:
            continue

        # Periodic refresh for the home surface and settings overlay.
        now = time.time()
        if (now - last_refresh) > 2.0 and not ui.confirm_delete:
            ui.refresh_hosts()
            ui.draw_current()
            last_refresh = now
        if not ui.visible:
            ui._start_live_probe()

        if menu_sock in ready:
            try:
                msg = menu_sock.recv(16)
            except OSError:
                msg = b""
            if msg and msg[0] == CMD_TOGGLE:
                ui.toggle()

        if ui.touch.fd is not None and ui.touch.fileno() in ready:
            events = ui.touch.read_events()
            for kind, a, b in events:
                if kind == "up":
                    ui.on_tap(a, b)


if __name__ == "__main__":
    raise SystemExit(main())
