#!/usr/bin/env python3
"""Generate the social preview card into static/og-image.png.

This is the image that appears when the site is shared on LinkedIn, Slack, iMessage or
anywhere else that renders a link preview. Without it those previews show a bare URL,
which reads as an unfinished site.

1200x630 is the size every major platform crops toward. Text is kept well inside a margin
because some clients crop to a squarer aspect, and it is set large: most previews are
rendered small, so anything subtle disappears.

Typography only, no symbol. See the comment in main() for why.

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

# Georgia, a serif.
#
# A serif at all because nearly every personal site in this field ships a sans-serif card,
# so it reads as a considered choice rather than a default, and it suits a site that is
# essays rather than a product.
#
# Georgia specifically, over Constantia which looks better at this size, because the same
# face sets the favicon and Georgia was drawn for small on-screen sizes. Constantia's fine
# serifs blurred into a smudge at 16px. One typeface across both needs no explaining, and
# the difference at card size is marginal where the difference at favicon size is not.
#
# Fallbacks stay in the serif family, so the card degrades to another serif rather than to
# something with a different voice.
FONTS_BOLD = [
    r"C:\Windows\Fonts\georgiab.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
]
FONTS_REG = [
    r"C:\Windows\Fonts\georgia.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
]


def font(candidates, size):
    for c in candidates:
        if Path(c).is_file():
            return ImageFont.truetype(c, size)
    sys.exit("No suitable font found")


def main() -> int:
    img = Image.new("RGB", (W, H), BG[:3])
    d = ImageDraw.Draw(img)

    margin = 96

    # A plain rule above the name instead of a symbol. Every simple security glyph is
    # taken -- lock, key, shield, keyhole -- and each attempt to simplify one drifted
    # toward some other standard icon. A line carries no meaning to misread, and nobody
    # owns it.
    d.rectangle([margin, 196, margin + 132, 204], fill=FG)

    name_f = font(FONTS_BOLD, 88)
    tag_f = font(FONTS_REG, 44)
    dom_f = font(FONTS_REG, 32)

    d.text((margin, 250), NAME, font=name_f, fill=FG)
    d.text((margin, 372), TAGLINE, font=tag_f, fill=MUTED)
    d.text((margin, H - 114), DOMAIN, font=dom_f, fill=MUTED)

    STATIC.mkdir(exist_ok=True)
    img.save(OUT, optimize=True)
    print(f"  wrote {OUT.relative_to(STATIC.parent)} ({W}x{H}, {OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
