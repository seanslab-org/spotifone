#!/bin/sh
# Spotifone Display Setup
# Disables fbcon and writes pre-converted logo to framebuffer.
#
# Usage: /opt/spotifone/scripts/setup_display.sh

# Prefer boot.fb (has "loading..." text) over logo.fb (runtime wallpaper).
LOGO_FB="/opt/spotifone/boot.fb"
if [ ! -f "$LOGO_FB" ]; then
    LOGO_FB="/opt/spotifone/logo.fb"
fi

if [ ! -f "$LOGO_FB" ]; then
    echo "WARNING: no boot.fb or logo.fb found, skipping display setup"
    exit 0
fi

# Unbind all fbcon vtconsoles so console text doesn't overwrite the logo
for vt in /sys/class/vtconsole/vtcon*/bind; do
    [ -f "$vt" ] && echo 0 > "$vt" 2>/dev/null
done

# Hide cursor
echo -e '\033[?25l' > /dev/tty1 2>/dev/null

if command -v python3 >/dev/null 2>&1; then
python3 - "$LOGO_FB" <<'PY'
import array
import errno
import fcntl
import os
import struct
from pathlib import Path

import sys
LOGO_FB = sys.argv[1] if len(sys.argv) > 1 else "/opt/spotifone/boot.fb"
FB_DEV = "/dev/fb0"
FB_WIDTH = 480
FB_HEIGHT = 800
FB_BPP = 32
FB_STRIDE = FB_WIDTH * (FB_BPP // 8)  # 1920
PAGE_LEN = FB_STRIDE * FB_HEIGHT      # 1,536,000

# ioctl numbers (linux/fb.h)
FBIOGET_VSCREENINFO = 0x4600
FBIOPUT_VSCREENINFO = 0x4601
FBIOBLANK = 0x4611

# sysfs paths
PAN_PATH = Path("/sys/class/graphics/fb0/pan")
VER_UPDATE_PAN = Path("/sys/class/graphics/fb0/ver_update_pan")
OSD_ALPHA = Path("/sys/class/graphics/fb0/osd_plane_alpha")
WINDOW_AXIS = Path("/sys/class/graphics/fb0/window_axis")


def _sysfs_write(p: Path, val: str) -> None:
    try:
        if p.exists():
            p.write_text(val)
    except Exception:
        pass


def _seek(fd: int, off: int) -> None:
    while True:
        try:
            os.lseek(fd, off, os.SEEK_SET)
            return
        except OSError as e:
            if e.errno == errno.EINTR:
                continue
            raise


def _write_all(fd: int, data: memoryview) -> None:
    written = 0
    total = len(data)
    while written < total:
        try:
            n = os.write(fd, data[written:])
        except OSError as e:
            if e.errno == errno.EINTR:
                continue
            raise
        if n <= 0:
            raise OSError("short write to framebuffer")
        written += n


logo = Path(LOGO_FB).read_bytes()
if len(logo) != PAGE_LEN:
    print(f"WARNING: logo.fb size {len(logo)} != expected {PAGE_LEN}; skipping")
    raise SystemExit(0)

logo_view = memoryview(logo)

fd = os.open(FB_DEV, os.O_RDWR)
try:
    # Switch to 32bpp BGRA.  The Amlogic OSD in 24bpp mode leaves alpha
    # bits undefined, causing random pixel noise.  32bpp with A=0xFF per
    # pixel gives a clean image.
    buf = array.array("B", bytes(160))
    fcntl.ioctl(fd, FBIOGET_VSCREENINFO, buf)
    struct.pack_into("I", buf, 24, 32)       # bits_per_pixel = 32
    struct.pack_into("III", buf, 32, 16, 8, 0)  # red:   offset=16, len=8
    struct.pack_into("III", buf, 44, 8, 8, 0)   # green: offset=8,  len=8
    struct.pack_into("III", buf, 56, 0, 8, 0)   # blue:  offset=0,  len=8
    struct.pack_into("III", buf, 68, 24, 8, 0)  # alpha: offset=24, len=8
    fcntl.ioctl(fd, FBIOPUT_VSCREENINFO, buf)

    # Write logo in small chunks.
    _seek(fd, 0)
    off = 0
    while off < PAGE_LEN:
        chunk = min(4096, PAGE_LEN - off)
        _write_all(fd, logo_view[off : off + chunk])
        off += chunk

    # Enable the OSD plane (it starts disabled after boot).
    fcntl.ioctl(fd, FBIOBLANK, 0)
finally:
    os.close(fd)

# OSD configuration: fully opaque, correct window, pin pan.
_sysfs_write(OSD_ALPHA, "0x300")
_sysfs_write(WINDOW_AXIS, "0 0 479 799")
_sysfs_write(PAN_PATH, "0,0")

# Disable other OSD planes so they don't blend garbage over fb0.
for other in ("fb1", "fb2"):
    _sysfs_write(Path(f"/sys/class/graphics/{other}/osd_plane_alpha"), "0x000")

_sysfs_write(VER_UPDATE_PAN, "1")
print("Display: logo written (32bpp BGRA)")
PY
else
    echo "WARNING: python3 missing; skipping framebuffer write"
fi
