#!/usr/bin/env python3
"""Generate text-only boot/background assets for Car Thing display.

Design goal: walkie.sh-inspired, text-first UI with Spotifone purple accent.
No icon/bitmap art; just styled text blocks.

Outputs:
  1) logo.fb    — raw BGR888 for runtime framebuffer (/dev/fb0), 480x800
  2) bootup.bmp — 16-bit R5G6B5 BMP for Amlogic /dev/logo partition, 480x800

Usage:
    python3 scripts/convert_logo.py [output.fb]
"""

import struct
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Error: Pillow required. Install with: pip3 install Pillow")
    sys.exit(1)

FB_WIDTH = 480
FB_HEIGHT = 800
BPP = 4  # 32-bit BGRA8888

# Walkie-inspired palette (RGB tuples for Pillow)
BG = (0x0A, 0x0A, 0x0A)       # #0a0a0a
SURFACE = (0x14, 0x14, 0x14)  # #141414
BORDER = (0x22, 0x22, 0x22)   # #222222
TEXT = (0xE0, 0xE0, 0xE0)     # #e0e0e0
MUTED = (0x88, 0x88, 0x88)    # #888888
ACCENT = (0x6E, 0x56, 0xCF)   # #6E56CF (Spotifone purple)

# Font paths (macOS system fonts, with fallbacks).
SANS_FONT_PATHS = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
MONO_FONT_PATHS = [
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Supplemental/Menlo.ttc",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
]


def find_font(paths: list[str], size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Find a usable system font, falling back to Pillow default.

    Note: .ttc collections may have multiple faces; we try index=1 for bold.
    """
    for path in paths:
        try:
            index = 1 if bold and path.endswith(".ttc") else 0
            return ImageFont.truetype(path, size, index=index)
        except (OSError, IndexError):
            continue
    print("WARNING: No system font found, using Pillow default")
    return ImageFont.load_default()


def _center_x(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    return (FB_WIDTH - w) // 2


def rgb_to_bgra_bytes(canvas: Image.Image) -> bytes:
    """Convert RGB Pillow image to BGRA8888 raw bytes (alpha=0xFF).

    The Amlogic S905D2 OSD driver needs 32bpp with explicit alpha; 24bpp
    leaves alpha bits undefined which shows as pixel noise on the panel.
    """
    r, g, b = canvas.split()
    a = Image.new("L", canvas.size, 255)
    bgra = Image.merge("RGBA", (b, g, r, a))
    return bgra.tobytes()


def save_r5g6b5_bmp(canvas: Image.Image, path: str) -> None:
    """Save image as 16-bit R5G6B5 BMP (Amlogic boot logo format).

    BMP with BITMAPINFOHEADER + BI_BITFIELDS masks for R5G6B5.
    Bottom-up row order (standard BMP).
    """
    w, h = canvas.size
    row_bytes = w * 2
    # BMP rows padded to 4-byte boundary
    row_pad = (4 - (row_bytes % 4)) % 4
    stride = row_bytes + row_pad

    # Pixel data size
    pixel_size = stride * h

    # Header sizes: 14 (file hdr) + 40 (info hdr) + 12 (3x DWORD masks)
    hdr_size = 14 + 40 + 12
    file_size = hdr_size + pixel_size

    buf = bytearray()

    # BITMAPFILEHEADER (14 bytes)
    buf += struct.pack('<2sIHHI', b'BM', file_size, 0, 0, hdr_size)

    # BITMAPINFOHEADER (40 bytes)
    buf += struct.pack('<IiiHHIIiiII',
                       40,          # biSize
                       w,           # biWidth
                       h,           # biHeight (positive = bottom-up)
                       1,           # biPlanes
                       16,          # biBitCount
                       3,           # biCompression = BI_BITFIELDS
                       pixel_size,  # biSizeImage
                       0, 0,        # biXPelsPerMeter, biYPelsPerMeter
                       0, 0)        # biClrUsed, biClrImportant

    # Color masks: R5 G6 B5
    buf += struct.pack('<III', 0xF800, 0x07E0, 0x001F)

    # Pixel data (bottom-up)
    pixels = canvas.load()
    for y in range(h - 1, -1, -1):
        for x in range(w):
            r, g, b = pixels[x, y][:3]
            val = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
            buf += struct.pack('<H', val)
        buf += b'\x00' * row_pad

    Path(path).write_bytes(bytes(buf))
    print(f"Boot BMP: {path} ({len(buf)} bytes, {w}x{h} R5G6B5)")


def render_canvas() -> Image.Image:
    """Render the 480x800 text-only boot/background canvas."""
    canvas = Image.new("RGB", (FB_WIDTH, FB_HEIGHT), BG)
    draw = ImageDraw.Draw(canvas)

    # Top accent rule (matches menu header)
    draw.rectangle((0, 0, FB_WIDTH, 6), fill=ACCENT)

    title_font = find_font(SANS_FONT_PATHS, 74, bold=True)
    tagline_font = find_font(SANS_FONT_PATHS, 26, bold=False)
    mono_font = find_font(MONO_FONT_PATHS, 22, bold=False)
    small_font = find_font(SANS_FONT_PATHS, 20, bold=False)

    # Hero title with purple accent on "one" (walkie-style span)
    left = "spotif"
    right = "one"
    left_bbox = draw.textbbox((0, 0), left, font=title_font)
    right_bbox = draw.textbbox((0, 0), right, font=title_font)
    title_w = (left_bbox[2] - left_bbox[0]) + (right_bbox[2] - right_bbox[0])
    title_x = (FB_WIDTH - title_w) // 2
    title_y = 135
    draw.text((title_x, title_y), left, fill=TEXT, font=title_font)
    draw.text((title_x + (left_bbox[2] - left_bbox[0]), title_y), right, fill=ACCENT, font=title_font)

    # Tagline (centered, muted)
    tagline_1 = "bluetooth mic + keyboard."
    tagline_2 = "no setup. just talk."
    draw.text((_center_x(draw, tagline_1, tagline_font), 235), tagline_1, fill=MUTED, font=tagline_font)
    draw.text((_center_x(draw, tagline_2, tagline_font), 268), tagline_2, fill=MUTED, font=tagline_font)

    # "Install box" equivalent with key hints (monospace)
    box_w = 420
    box_h = 140
    box_x0 = (FB_WIDTH - box_w) // 2
    box_y0 = 340
    draw.rectangle((box_x0, box_y0, box_x0 + box_w, box_y0 + box_h), fill=SURFACE, outline=BORDER, width=1)
    # Small accent rule inside the box
    draw.rectangle((box_x0, box_y0, box_x0 + box_w, box_y0 + 4), fill=ACCENT)

    hints = [
        "$ mute  -> menu",
        "$ wheel -> app switch",
        "$ round -> ptt",
    ]
    hy = box_y0 + 28
    for line in hints:
        draw.text((box_x0 + 22, hy), line, fill=TEXT, font=mono_font)
        hy += 34

    # Footer (muted)
    footer = "https://seanslab.org"
    draw.text((_center_x(draw, footer, small_font), 710), footer, fill=MUTED, font=small_font)

    return canvas


def convert_logo(output_path: str) -> None:
    canvas = render_canvas()

    # Save preview PNG
    preview_path = Path(output_path).with_suffix(".png")
    canvas.save(str(preview_path))
    print(f"Preview: {preview_path}")

    # Runtime framebuffer: BGRA8888
    bgra_data = rgb_to_bgra_bytes(canvas)
    Path(output_path).write_bytes(bgra_data)
    print(f"Output: {output_path} ({len(bgra_data)} bytes)")

    # Boot logo: 16-bit R5G6B5 BMP for Amlogic logo partition
    bmp_path = str(Path(output_path).with_name("bootup.bmp"))
    save_r5g6b5_bmp(canvas, bmp_path)


if __name__ == "__main__":
    project_dir = Path(__file__).resolve().parent.parent
    output_file = sys.argv[1] if len(sys.argv) > 1 else str(project_dir / "logo.fb")
    convert_logo(output_file)
