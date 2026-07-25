#!/usr/bin/env python3
"""Generate the social preview card into static/og-image.png.

This is the image that appears when the site is shared on LinkedIn, Slack, iMessage or
anywhere else that renders a link preview. Without it those previews show a bare URL,
which reads as an unfinished site.

1200x630 is the size every major platform crops toward. Text is kept well inside a margin
because some clients crop to a squarer aspect, and it is set large: most previews are
rendered small, so anything subtle disappears.

Deliberately matches the favicon -- same near-black, same keyhole -- so the tab icon and
the link card look like the same site.

Requires Pillow. Not part of the build: run it by hand when the design changes, and commit
the output.

    python scripts/make_og_image.py
"""

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("Pillow is required: pip install Pillow")

STATIC = Path(__file__).resolve().parent.parent / "static"
OUT = STATIC / "og-image.png"

W, H = 1200, 630
BG = (29, 30, 32, 255)
FG = (255, 255, 255, 255)
MUTED = (155, 162, 172, 255)

NAME = "Karsten Huttelmaier"
TAGLINE = "Identity and security systems."
DOMAIN = "kphutt.com"

FONTS_BOLD = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
FONTS_REG = [
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def font(candidates, size):
    for c in candidates:
        if Path(c).is_file():
            return ImageFont.truetype(c, size)
    sys.exit("No suitable font found")


def keyhole(draw, cx, cy, scale):
    """The same mark as the favicon, drawn at an arbitrary size and position."""
    bore_r = 34 * scale
    draw.ellipse([cx - bore_r, cy - bore_r, cx + bore_r, cy + bore_r], fill=FG)
    draw.polygon(
        [
            (cx - 12 * scale, cy + 22 * scale),
            (cx + 12 * scale, cy + 22 * scale),
            (cx + 15 * scale, cy + 74 * scale),
            (cx - 15 * scale, cy + 74 * scale),
        ],
        fill=FG,
    )


def main() -> int:
    img = Image.new("RGB", (W, H), BG[:3])
    d = ImageDraw.Draw(img)

    margin = 90
    keyhole(d, margin + 46, 150, 1.0)

    name_f = font(FONTS_BOLD, 76)
    tag_f = font(FONTS_REG, 40)
    dom_f = font(FONTS_REG, 32)

    y = 300
    d.text((margin, y), NAME, font=name_f, fill=FG)
    y += 100
    d.text((margin, y), TAGLINE, font=tag_f, fill=MUTED)

    # A rule and the domain, bottom-left, so the card reads as belonging somewhere.
    d.line([(margin, H - 118), (margin + 120, H - 118)], fill=MUTED, width=3)
    d.text((margin, H - 100), DOMAIN, font=dom_f, fill=MUTED)

    STATIC.mkdir(exist_ok=True)
    img.save(OUT, optimize=True)
    print(f"  wrote {OUT.relative_to(STATIC.parent)} ({W}x{H}, {OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
