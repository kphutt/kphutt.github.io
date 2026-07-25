#!/usr/bin/env python3
"""Generate the site's favicon set into static/.

The theme's head partial links five icon files by default. They were never created, so
every page load fired five requests that 404'd and the site showed no icon in a browser
tab. This script produces them from one definition, so the sizes cannot drift apart.

The mark is a lowercase k, set in the same serif as the social card.

It was a keyhole first. That was dropped because it read as generic security branding --
and every simpler symbol tried in its place landed on some other stock glyph: a notched
disc became Pac-Man, stacked bars became a hamburger menu, a dot under an arc became the
default user avatar. Simple symbols are nearly all claimed. An initial is not: nobody else
has this name, so it cannot be mistaken for another outfit's logo.

Replacing it means dropping better artwork into static/ under the same filenames, or
pointing params.assets.* in hugo.toml somewhere else.

Requires Pillow. Not part of the build or CI: run it by hand when the icon changes, and
commit the output.

    python scripts/make_favicons.py
"""

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("Pillow is required: pip install Pillow")

STATIC = Path(__file__).resolve().parent.parent / "static"

# PaperMod's light theme sits on white with near-black text; these match it rather than
# introducing a third colour.
BG = (29, 30, 32, 255)
FG = (255, 255, 255, 255)

# Rendered large and downsampled, which gives cleaner edges at 16px than drawing small.
MASTER = 512

LETTER = "k"

# Georgia Bold: the same face as the social card, so the tab and the link preview share a
# voice, and the one serif here that survives 16px. Georgia was drawn for small on-screen
# sizes; Constantia was tried first and its thin strokes and fine serifs blurred into an
# illegible smudge at that size. Fallbacks stay in the serif family rather than dropping
# to a sans.
FONTS = [
    r"C:\Windows\Fonts\georgiab.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
]

# Set large in the tile. A letterform at 16px has almost no room, so the glyph has to fill
# nearly the whole square -- the generous padding that suits a symbol destroys a letter.
LETTER_FILL = 0.82


def load_font(size: int):
    for candidate in FONTS:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    sys.exit("No suitable serif found; add one to FONTS")


def render_master() -> Image.Image:
    img = Image.new("RGBA", (MASTER, MASTER), BG)
    draw = ImageDraw.Draw(img)
    font = load_font(int(MASTER * LETTER_FILL))
    # Centre on the glyph's actual ink, not its advance box. A lowercase k has a tall
    # ascender and no descender, so its metrics box is badly off-centre; centring on that
    # box leaves the letter visibly high in the tile.
    left, top, right, bottom = draw.textbbox((0, 0), LETTER, font=font)
    x = (MASTER - (right - left)) / 2 - left
    y = (MASTER - (bottom - top)) / 2 - top
    draw.text((x, y), LETTER, font=font, fill=FG)
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
