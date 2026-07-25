#!/usr/bin/env python3
"""Generate the site's favicon set into static/.

The theme's head partial links five icon files by default. They were never created, so
every page load fired five requests that 404'd and the site showed no icon in a browser
tab. This script produces them from one definition, so the sizes cannot drift apart.

The mark is a keyhole. It was chosen for legibility at 16px, which is the only size that
really matters: a solid field with one cutout survives downsampling, where thin strokes
and letterforms turn to mush. Replacing it means dropping better artwork into static/
under the same filenames, or pointing params.assets.* in hugo.toml somewhere else.

Requires Pillow. Not part of the build or CI: run it by hand when the icon changes, and
commit the output.

    python scripts/make_favicons.py
"""

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("Pillow is required: pip install Pillow")

STATIC = Path(__file__).resolve().parent.parent / "static"

# PaperMod's light theme sits on white with near-black text; these match it rather than
# introducing a third colour.
BG = (29, 30, 32, 255)
FG = (255, 255, 255, 255)

# Rendered large and downsampled, which gives cleaner edges at 16px than drawing small.
MASTER = 512

# Keyhole geometry, in MASTER-sized coordinates.
#
# The mark is the keyhole itself, drawn light on dark, rather than a light panel with a
# keyhole cut out of it. Both were tried. The panel version put the glyph inside a border,
# which cost roughly a third of the tile, and at 16px the bore and slot then merged into a
# single vertical bar that read as the digit 1. Drawing the glyph directly lets it fill the
# tile, keeps the bore visibly round at 16px, and means these icons and the Safari mask in
# static/safari-pinned-tab.svg are the same shape rather than inverses of each other.
#
# The bore is deliberately much wider than the slot. That contrast is the only thing
# separating the two features once each is a few pixels across.
BORE = (150, 110, 362, 322)                              # the round part
SLOT = [(222, 305), (290, 305), (306, 420), (206, 420)]  # the tapered part below it


def render_master() -> Image.Image:
    img = Image.new("RGBA", (MASTER, MASTER), BG)
    draw = ImageDraw.Draw(img)
    draw.ellipse(list(BORE), fill=FG)
    draw.polygon(SLOT, fill=FG)
    return img


def rounded(img: Image.Image, radius_ratio: float = 0.18) -> Image.Image:
    """Round the corners so the apple-touch icon does not look like a hard square."""
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [(0, 0), (img.size[0] - 1, img.size[1] - 1)],
        radius=int(img.size[0] * radius_ratio),
        fill=255,
    )
    out = img.copy()
    out.putalpha(mask)
    return out


def main() -> int:
    STATIC.mkdir(exist_ok=True)
    master = render_master()

    written = []

    for size in (16, 32):
        path = STATIC / f"favicon-{size}x{size}.png"
        master.resize((size, size), Image.LANCZOS).save(path)
        written.append(path)

    # Apple rounds the corners itself, but rounding here keeps it correct anywhere that
    # does not, and costs nothing.
    apple = STATIC / "apple-touch-icon.png"
    rounded(master.resize((180, 180), Image.LANCZOS)).save(apple)
    written.append(apple)

    # A single .ico carrying the three sizes Windows and older browsers ask for.
    ico = STATIC / "favicon.ico"
    master.save(ico, sizes=[(16, 16), (32, 32), (48, 48)])
    written.append(ico)

    for path in written:
        print(f"  wrote {path.relative_to(STATIC.parent)} ({path.stat().st_size} bytes)")

    print("\nsafari-pinned-tab.svg is hand-written (a monochrome mask, not a raster) and")
    print("is not generated here -- see the file itself.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
