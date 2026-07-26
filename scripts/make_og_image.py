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

    # No mark. Every simple security glyph is taken -- lock, key, shield, keyhole -- and
    # each attempt to simplify one landed on a different stock icon instead. The name is
    # the only element here that nobody else has, so it carries the card alone.
    #
    # The block sits optically centred rather than dropped to the lower half. An earlier
    # version kept the old composition and simply deleted the mark, which left an obvious
    # hole in the top-left corner the mark had been holding.
    name_f = font(FONTS_BOLD, 76)
    tag_f = font(FONTS_REG, 40)
    dom_f = font(FONTS_REG, 30)

    d.text((margin, 232), NAME, font=name_f, fill=FG)
    d.text((margin, 336), TAGLINE, font=tag_f, fill=MUTED)

    # The rule separates the domain from the rest, and is the same element used as the
    # favicon -- so the tab and the preview share their one piece of furniture.
    d.rectangle([margin, 410, margin + 110, 415], fill=MUTED)
    d.text((margin, 430), DOMAIN, font=dom_f, fill=MUTED)

    STATIC.mkdir(exist_ok=True)
    img.save(OUT, optimize=True)
    print(f"  wrote {OUT.relative_to(STATIC.parent)} ({W}x{H}, {OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
