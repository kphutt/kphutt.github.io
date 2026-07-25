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

    if failures:
        print(f"Build check FAILED ({len(failures)} problem(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"Build check passed: all URLs use {base}, canonical correct, CNAME intact.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
