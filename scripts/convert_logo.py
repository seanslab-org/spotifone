#!/usr/bin/env python3
"""Convert logo.jpeg to a vertical boot screen for Car Thing display.

Extracts the mic icon from logo.jpeg, places it centered on the upper
portion of a 480x800 black canvas, and draws "Spotifone" + tagline text
below. Output is raw BGR888 for the framebuffer.

Framebuffer: 480x800, 24bpp BGR888.
Output: logo.fb (480 * 800 * 3 = 1,152,000 bytes).

Usage:
    python3 scripts/convert_logo.py [input.jpeg] [output.fb]
"""

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Error: Pillow required. Install with: pip3 install Pillow")
    sys.exit(1)

FB_WIDTH = 480
FB_HEIGHT = 800
BPP = 3  # 24-bit BGR888

# Layout constants
ICON_WIDTH = 220
ICON_Y = 150  # Top of icon
TITLE_Y = 480
TAGLINE_Y = 555
TITLE_COLOR = (255, 255, 255)      # White
TAGLINE_COLOR = (130, 130, 130)    # Gray
BG_COLOR = (0, 0, 0)              # Black

# Font paths (macOS system fonts, with fallbacks)
FONT_PATHS = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def find_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Find a usable system font, falling back to Pillow default."""
    for path in FONT_PATHS:
        try:
            # .ttc files have multiple faces; index 0 = regular, 1 = bold
            index = 1 if bold and path.endswith(".ttc") else 0
            return ImageFont.truetype(path, size, index=index)
        except (OSError, IndexError):
            continue
    # Last resort: Pillow default (bitmap, ignores size param)
    print("WARNING: No system font found, using Pillow default")
    return ImageFont.load_default()


def crop_icon(img: Image.Image) -> Image.Image:
    """Crop the mic icon from the left portion of the logo.

    The logo is a horizontal layout: mic icon on left ~40%, text on right.
    We crop the icon region, remove the white background, and trim.
    """
    # Crop left 33% where the mic icon lives (avoid text bleed from right)
    icon_region = img.crop((0, 0, int(img.width * 0.33), img.height))

    # Convert to RGBA and build alpha mask from luminance
    # White/near-white pixels (R>220 AND G>220 AND B>220) become transparent
    icon_rgba = icon_region.convert("RGBA")
    r, g, b, a = icon_rgba.split()

    # Create mask: 0 where all channels > 220 (white bg), 255 elsewhere
    # Use point() for fast per-channel thresholding (no Python loop)
    r_mask = r.point(lambda v: 0 if v > 220 else 255)
    g_mask = g.point(lambda v: 0 if v > 220 else 255)
    b_mask = b.point(lambda v: 0 if v > 220 else 255)

    # Combine: pixel is opaque only if ANY channel is <= 220
    from PIL import ImageChops
    alpha = ImageChops.lighter(ImageChops.lighter(r_mask, g_mask), b_mask)
    icon_rgba.putalpha(alpha)

    # Trim transparent edges
    bbox = icon_rgba.getbbox()
    if bbox:
        icon_rgba = icon_rgba.crop(bbox)

    return icon_rgba


def rgb_to_bgr_bytes(canvas: Image.Image) -> bytes:
    """Convert RGB Pillow image to BGR888 raw bytes using channel swap."""
    r, g, b = canvas.split()
    bgr = Image.merge("RGB", (b, g, r))
    return bgr.tobytes()


def convert_logo(input_path: str, output_path: str) -> None:
    img = Image.open(input_path).convert("RGB")
    print(f"Input: {img.size[0]}x{img.size[1]}")

    # Extract and scale mic icon
    icon = crop_icon(img)
    scale = ICON_WIDTH / icon.width
    icon_h = int(icon.height * scale)
    icon = icon.resize((ICON_WIDTH, icon_h), Image.LANCZOS)
    print(f"Icon: {ICON_WIDTH}x{icon_h}")

    # Create black canvas
    canvas = Image.new("RGB", (FB_WIDTH, FB_HEIGHT), BG_COLOR)

    # Paste icon centered horizontally
    icon_x = (FB_WIDTH - ICON_WIDTH) // 2
    # Composite RGBA icon onto RGB canvas (handles transparency)
    canvas.paste(icon, (icon_x, ICON_Y), icon)
    print(f"Icon placed at ({icon_x}, {ICON_Y})")

    # Draw text
    draw = ImageDraw.Draw(canvas)

    title_font = find_font(64, bold=True)
    tagline_font = find_font(28)

    # "Spotifone" — centered
    title_text = "Spotifone"
    title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
    title_w = title_bbox[2] - title_bbox[0]
    title_x = (FB_WIDTH - title_w) // 2
    draw.text((title_x, TITLE_Y), title_text, fill=TITLE_COLOR, font=title_font)
    print(f"Title at ({title_x}, {TITLE_Y}), width={title_w}")

    # "Music · Voice · Connected" — centered
    tagline_text = "Music \u00b7 Voice \u00b7 Connected"
    tag_bbox = draw.textbbox((0, 0), tagline_text, font=tagline_font)
    tag_w = tag_bbox[2] - tag_bbox[0]
    tag_x = (FB_WIDTH - tag_w) // 2
    draw.text((tag_x, TAGLINE_Y), tagline_text, fill=TAGLINE_COLOR, font=tagline_font)
    print(f"Tagline at ({tag_x}, {TAGLINE_Y}), width={tag_w}")

    # Save a preview PNG for verification
    preview_path = Path(output_path).with_suffix(".png")
    canvas.save(str(preview_path))
    print(f"Preview: {preview_path}")

    # Convert to BGR888 and write
    bgr_data = rgb_to_bgr_bytes(canvas)
    Path(output_path).write_bytes(bgr_data)
    print(f"Output: {output_path} ({len(bgr_data)} bytes)")


if __name__ == "__main__":
    project_dir = Path(__file__).resolve().parent.parent
    input_file = sys.argv[1] if len(sys.argv) > 1 else str(project_dir / "logo.jpeg")
    output_file = sys.argv[2] if len(sys.argv) > 2 else str(project_dir / "logo.fb")
    convert_logo(input_file, output_file)
