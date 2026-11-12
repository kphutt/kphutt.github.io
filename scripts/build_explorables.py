#!/usr/bin/env python3
"""Build the directory the explorables image serves: explorables/ without its READMEs,
plus a robots.txt and a sitemap.xml.

    python scripts/build_explorables.py [OUT]     # OUT defaults to public-explorables/

Every README.md is for the repository and is never served, so it is left out here. Nothing
else is changed or dropped: a stray file is copied as it is, and scripts/check_explorables.py
fails on it, rather than vanishing quietly. OUT is emptied first, so a file deleted from
explorables/ cannot outlive it in a working tree (the same reason scripts/local_verify.py
empties public/).

robots.txt and sitemap.xml are generated, never written by hand, so a new explorable is in
the sitemap the moment its folder exists. The sitemap lists the landing page and each
explorable's index.html (explorables/<slug>/index.html), by absolute URL on ORIGIN.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from xml.sax.saxutils import escape

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "explorables"
DEFAULT_OUT = REPO / "public-explorables"

# The explorables origin. Its host name is the domain mapping in infra/explorables.tf.
ORIGIN = "https://explorables.kphutt.com"

GENERATED = ("robots.txt", "sitemap.xml")


def is_readme(name: str) -> bool:
    return name.lower() == "readme.md"


def page_paths(source: Path) -> list[str]:
    """The URL path of every page the sitemap lists: the landing page, then each explorable."""
    slugs = sorted(p.name for p in source.iterdir() if p.is_dir() and (p / "index.html").is_file())
    return ["/"] + [f"/{slug}/" for slug in slugs]


def robots_txt() -> str:
    return f"User-agent: *\nAllow: /\n\nSitemap: {ORIGIN}/sitemap.xml\n"


def sitemap_xml(paths: list[str]) -> str:
    urls = "".join(f"  <url><loc>{escape(ORIGIN + path)}</loc></url>\n" for path in paths)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{urls}</urlset>\n")


def build(source: Path = SOURCE, out: Path = DEFAULT_OUT) -> None:
    clash = [name for name in GENERATED if (source / name).exists()]
    if clash:
        sys.exit(f"FAIL: {', '.join(clash)} in explorables/ -- the build generates these, so delete them")
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(source, out, ignore=lambda _dir, names: [n for n in names if is_readme(n)])
    (out / "robots.txt").write_text(robots_txt(), encoding="utf-8", newline="\n")
    (out / "sitemap.xml").write_text(sitemap_xml(page_paths(source)), encoding="utf-8", newline="\n")


def main() -> int:
    out = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_OUT
    if not SOURCE.is_dir():
        sys.exit(f"FAIL: {SOURCE} not found")
    build(SOURCE, out)
    print(f"Built {out.name}/ from explorables/ (READMEs left out, robots.txt and sitemap.xml generated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
