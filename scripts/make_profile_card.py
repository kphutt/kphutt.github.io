#!/usr/bin/env python3
"""Generate the wide link-preview card, static/images/kphutt-wide.jpg (the og:image).

The square headshot (static/images/kphutt.jpg) fills the left 630x630 with a straight edge.
The right side is one flat colour from the site's light theme with the name, a one-line
description and the domain. LinkedIn and Facebook crop previews to a wide card, so a wide
image keeps the whole head in frame.

Fonts and the muted/ink colours follow scripts/make_og_image.py and PaperMod's light theme
(--code-bg, --primary, --secondary in themes/PaperMod/assets/css/core/theme-vars.css).

Requires Pillow. Not part of the build: run it by hand when the text or photo changes, and
commit the output.

    python scripts/make_profile_card.py
"""

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("Pillow is required: pip install Pillow")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_og_image import FONTS_BOLD, FONTS_REG, font  # noqa: E402

STATIC = Path(__file__).resolve().parent.parent / "static"
PHOTO = STATIC / "images" / "kphutt.jpg"
OUT = STATIC / "images" / "kphutt-wide.jpg"

W, H = 1200, 630
BG = (245, 245, 245)       # PaperMod light --code-bg
INK = (30, 30, 30)         # --primary
MUTED = (108, 108, 108)    # --secondary

NAME_LINES = ["Karsten", "Huttelmaier"]
TAGLINE_LINES = ["I build identity and", "security systems."]
DOMAIN = "kphutt.com"

MARGIN = 72                # between the photo and the text, and from the right and bottom edges


def main() -> int:
    img = Image.new("RGB", (W, H), BG)
    img.paste(Image.open(PHOTO).convert("RGB").resize((H, H), Image.LANCZOS), (0, 0))
    d = ImageDraw.Draw(img)

    x = H + MARGIN
    name_f, tag_f, dom_f = font(FONTS_BOLD, 66), font(FONTS_REG, 34), font(FONTS_REG, 28)
    name_lh, tag_lh, gap = 80, 46, 28
    block = len(NAME_LINES) * name_lh + gap + len(TAGLINE_LINES) * tag_lh
    y = (H - block) // 2 - 6
    for line in NAME_LINES:
        d.text((x, y), line, font=name_f, fill=INK)
        y += name_lh
    y += gap
    for line in TAGLINE_LINES:
        d.text((x, y), line, font=tag_f, fill=MUTED)
        y += tag_lh
    d.text((x, H - MARGIN - 20), DOMAIN, font=dom_f, fill=MUTED)

    widest = max(d.textlength(t, font=f) for t, f in
                 [(NAME_LINES[1], name_f), (TAGLINE_LINES[0], tag_f)])
    if x + widest > W - MARGIN:
        sys.exit(f"text is clipped: {x + widest:.0f}px > {W - MARGIN}px")
    img.save(OUT, "JPEG", quality=90, optimize=True, subsampling=0)  # no EXIF is copied
    print(f"  wrote {OUT.relative_to(STATIC.parent)} ({W}x{H}, {OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
