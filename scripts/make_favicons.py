#!/usr/bin/env python3
"""Generate the site's favicon set into static/.

The theme's head partial links five icon files by default. They were never created, so
every page load fired five requests that 404'd and the site showed no icon in a browser
tab. This script produces them from one definition, so the sizes cannot drift apart.

The mark is a single horizontal rule -- the same element that separates the domain on the
social card.

It was a keyhole first, which read as generic security branding. Every simpler symbol
tried in its place landed on some other stock glyph: a notched disc became Pac-Man,
stacked bars became a hamburger menu, a dot under an arc became the default user avatar.
A lowercase initial was tried after that and blurred badly at 16px.

A rule is the end of that line of reasoning rather than another attempt at it. It depicts
nothing, so there is nothing for it to resemble.

Replacing it means dropping better artwork into static/ under the same filenames, or
pointing params.assets.* in hugo.toml somewhere else.

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

# A single horizontal rule -- the same element that separates the domain on the social
# card, so the tab and the link preview share their one piece of furniture.
#
# Proportions are set for 16px, which is the only size that constrains anything. The bar
# spans a wide fraction of the tile so it does not read as a stray dot, and stays thick
# enough to be a line rather than a hairline: at 16px these land on 10 x 2 pixels.
BAR_WIDTH = 0.62    # fraction of the tile
BAR_HEIGHT = 0.115  # fraction of the tile


def render(size: int) -> Image.Image:
    """Draw the tile at its final size, snapped to whole pixels.

    Deliberately NOT rendered large and downsampled, which is the usual advice and was
    what this did first. Resampling a hard-edged rectangle with Lanczos rings: at 16px the
    bar peaked at luminance 236 instead of 255, its ends faded through 218 and 39, and the
    rows above and below sat at 27 and 35 against a background of 29 -- a visible grey
    halo, and ten distinct colours for a two-colour shape.

    Downsampling earns its keep for a letterform or a curve. For an axis-aligned rectangle
    it only invents intermediate values. Rounding the geometry to integers instead gives
    exactly two colours at every size, with no fringe.
    """
    img = Image.new("RGBA", (size, size), BG)
    w = max(1, round(size * BAR_WIDTH))
    h = max(1, round(size * BAR_HEIGHT))
    x0 = (size - w) // 2
    y0 = (size - h) // 2
    # rectangle() draws inclusive bounds, hence the -1.
    ImageDraw.Draw(img).rectangle([x0, y0, x0 + w - 1, y0 + h - 1], fill=FG)
    return img


def bar_rect() -> tuple:
    """The bar in a 0-100 viewBox, as (x, y, width, height). One definition, two SVGs."""
    w, h = 100 * BAR_WIDTH, 100 * BAR_HEIGHT
    return round((100 - w) / 2, 3), round((100 - h) / 2, 3), round(w, 3), round(h, 3)


def write_svgs() -> list:
    """Write both SVG icons from the same constants as the rasters.

    Two files rather than one, because they have opposite requirements: the favicon is
    white on the dark tile, matching the PNGs, while the Safari pinned-tab mask must be a
    solid black silhouette on transparency, since Safari discards the colour and refills
    the shape with the user's accent colour.

    Generated rather than hand-written so a change to BAR_WIDTH or BAR_HEIGHT reaches all
    five icons at once. They were hand-written first, which meant three separate copies of
    the same rectangle that nothing kept in agreement.
    """
    x, y, w, h = bar_rect()
    r, g, b, _ = BG

    favicon = STATIC / "favicon.svg"
    favicon.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">\n'
        f'  <rect width="100" height="100" fill="rgb({r},{g},{b})"/>\n'
        f'  <rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#fff"/>\n'
        "</svg>\n",
        encoding="utf-8",
    )

    mask = STATIC / "safari-pinned-tab.svg"
    mask.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">\n'
        f'  <rect x="{x}" y="{y}" width="{w}" height="{h}" fill="black"/>\n'
        "</svg>\n",
        encoding="utf-8",
    )
    return [favicon, mask]


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

    written = []

    # Each size is drawn at that size. Nothing is resampled -- see render().
    for size in (16, 32):
        path = STATIC / f"favicon-{size}x{size}.png"
        render(size).save(path)
        written.append(path)

    # Apple rounds the corners itself, but rounding here keeps it correct anywhere that
    # does not, and costs nothing.
    apple = STATIC / "apple-touch-icon.png"
    rounded(render(180)).save(apple)
    written.append(apple)

    # A single .ico carrying the three sizes Windows and older browsers ask for. Each
    # frame is drawn at its own size and passed in explicitly, because saving one image
    # with sizes=[...] makes Pillow resample internally -- reintroducing exactly the
    # ringing that render() exists to avoid.
    ico = STATIC / "favicon.ico"
    frames = [render(s) for s in (16, 32, 48)]
    frames[-1].save(ico, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)],
                    append_images=frames[:-1])
    written.append(ico)

    written.extend(write_svgs())

    for path in written:
        print(f"  wrote {path.relative_to(STATIC.parent)} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
