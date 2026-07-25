#!/usr/bin/env python3
"""Check a built Hugo site before it can be deployed.

These are the things that have actually gone wrong here, turned into assertions. The
scheme check is the important one: the deploy workflow used to pass --baseURL taken from
the repo's GitHub Pages settings, which silently baked http:// into every canonical tag,
og:url, RSS link and sitemap entry while the site itself served fine over HTTPS. Nothing
failed, nothing looked broken, and the only way to notice was to read the shipped HTML.

Run it against a build directory:

    hugo --gc --minify
    python scripts/check_build.py public
"""

import re
import sys
from pathlib import Path

# Read the expected origin out of hugo.toml rather than hardcoding it, so this file does
# not become a second place the site's address has to be kept up to date.
CONFIG = Path(__file__).resolve().parent.parent / "hugo.toml"

TEXT_SUFFIXES = {".html", ".xml", ".txt", ".json", ".css", ".js"}


def expected_base_url() -> str:
    text = CONFIG.read_text(encoding="utf-8")
    match = re.search(r'^\s*baseURL\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        sys.exit(f"FAIL: no baseURL found in {CONFIG}")
    return match.group(1).rstrip("/")


def resolve_ref(ref: str, base: str, root: Path, page: Path):
    """Map a reference in the built HTML to the file it should resolve to.

    Returns None for anything not checkable here: other sites, mailto/data/anchors, and
    the RSS/sitemap URLs Hugo generates. External links are deliberately out of scope --
    they break for reasons outside this repo and would make the build fail on someone
    else's outage.
    """
    ref = ref.split("#", 1)[0].split("?", 1)[0].strip()
    if not ref:
        return None
    if ref.startswith(("mailto:", "data:", "javascript:", "tel:", "//")):
        return None

    if ref.startswith(base):
        ref = ref[len(base):] or "/"
    elif re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", ref):
        return None  # some other origin

    if ref.startswith("/"):
        target = root / ref.lstrip("/")
    else:
        target = page.parent / ref

    # A directory URL is served by its index.html.
    if ref.endswith("/") or (target.is_dir()):
        target = target / "index.html"
    return target


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "public")
    if not root.is_dir():
        sys.exit(f"FAIL: build directory not found: {root}")

    base = expected_base_url()
    if not base.startswith("https://"):
        sys.exit(f"FAIL: baseURL in hugo.toml is not https: {base}")

    host = base.split("://", 1)[1]
    wrong_scheme = f"http://{host}"

    failures = []

    # 1. No page may reference the site over plain http.
    offenders = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        count = body.count(wrong_scheme)
        if count:
            offenders.append((path.relative_to(root).as_posix(), count))
    if offenders:
        detail = ", ".join(f"{name} ({n})" for name, n in offenders)
        failures.append(f"found {wrong_scheme} in built output: {detail}")

    # 2. The homepage must advertise the canonical https URL.
    index = root / "index.html"
    if not index.is_file():
        failures.append("index.html missing from build output")
    else:
        html = index.read_text(encoding="utf-8", errors="replace")
        # Hugo minifies attributes, so the value may or may not be quoted.
        canonical = re.search(r'<link rel=["\']?canonical["\']?\s+href=["\']?([^"\'>\s]+)', html)
        if not canonical:
            failures.append("no canonical link found on the homepage")
        elif canonical.group(1).rstrip("/") != base:
            failures.append(f"canonical is {canonical.group(1)}, expected {base}/")

    # 3. static/CNAME must survive into the build, or the custom domain is dropped on deploy.
    cname = root / "CNAME"
    if not cname.is_file():
        failures.append("CNAME missing from build output -- custom domain would be lost")
    elif cname.read_text(encoding="utf-8").strip() != host:
        failures.append(f"CNAME says {cname.read_text().strip()!r}, expected {host!r}")

    # 4. Every internal reference must resolve to something in the build output.
    #
    # The theme's head partial links five icon files by default. None of them existed, so
    # every page load fired five 404s and the site had no tab icon -- for months, silently,
    # because a missing static file is not a build error. Anything the built HTML points at
    # on this site should exist.
    missing = {}
    for path in sorted(root.rglob("*.html")):
        html = path.read_text(encoding="utf-8", errors="replace")
        page = path.relative_to(root).as_posix()
        for ref in re.findall(r'(?:href|src)=["\']?([^"\'>\s]+)', html):
            target = resolve_ref(ref, base, root, path)
            if target is not None and not target.exists():
                missing.setdefault(target.relative_to(root).as_posix(), set()).add(page)
    if missing:
        for target, pages in sorted(missing.items()):
            shown = ", ".join(sorted(pages)[:3])
            more = f" (+{len(pages) - 3} more)" if len(pages) > 3 else ""
            failures.append(f"broken internal reference: {target} -- linked from {shown}{more}")

    if failures:
        print(f"Build check FAILED ({len(failures)} problem(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"Build check passed: all URLs use {base}, canonical correct, CNAME intact,")
    print("internal references all resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
