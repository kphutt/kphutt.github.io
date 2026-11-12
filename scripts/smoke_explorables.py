#!/usr/bin/env python3
"""Smoke-test a running copy of explorables.kphutt.com: every page answers, every header is right.

The deploy workflow runs it against the container on the CI runner, and against the Cloud
Run candidate revision before it gets any traffic, as scripts/smoke.py does for the site.

    python scripts/smoke_explorables.py http://127.0.0.1:8080
    python scripts/smoke_explorables.py https://cand-abc123---explorables-xyz-uw.a.run.app

What it checks, against BASE:
  - the landing page and its stylesheet return 200;
  - every explorables/<slug>/ returns 200, and /<slug> redirects to exactly /<slug>/;
  - every README path is a 404: the READMEs are for the repository and never served;
  - a missing page returns 404 with explorables/404.html, not the server's built-in page;
  - every one of those responses carries exactly the headers of the catch-all rule in
    server/explorables.sws.toml, Cache-Control and X-Robots-Tag included. The expected
    values are read from that file, so the config is the one place they are written down.

Standard library only.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from smoke import fetch  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "server" / "explorables.sws.toml"
SOURCE = REPO / "explorables"


def expected_headers() -> dict:
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    for rule in config["advanced"]["headers"]:
        if rule["source"] == "**":
            return dict(rule["headers"])
    sys.exit(f"FAIL: no catch-all '**' header rule in {CONFIG}")


def slugs() -> list[str]:
    return sorted(p.name for p in SOURCE.iterdir() if p.is_dir() and not p.name.startswith("."))


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    base = sys.argv[1].rstrip("/")
    want = expected_headers()
    failures = []

    def check(path, status=200, content_type=None, attempts=1):
        got, headers, body = fetch(base + path, attempts)
        if got != status:
            failures.append(f"{path}: status {got}, expected {status}")
        for name, value in want.items():
            if headers.get(name) != value:
                failures.append(f"{path}: {name} is {headers.get(name)!r}, expected {value!r}")
        if content_type and not (headers.get("Content-Type") or "").startswith(content_type):
            failures.append(f"{path}: Content-Type {headers.get('Content-Type')!r}, expected {content_type}")
        return headers, body

    # A cold instance or a tagged URL still being set up gets about thirty seconds.
    check("/", content_type="text/html", attempts=10)
    check("/index.css", content_type="text/css")
    check("/README.md", status=404, content_type="text/html")

    found = slugs()
    for slug in found:
        check(f"/{slug}/", content_type="text/html")
        check(f"/{slug}/README.md", status=404, content_type="text/html")
        got, headers, _ = fetch(f"{base}/{slug}")
        location = headers.get("Location", "")
        if got not in (301, 308) or location != f"/{slug}/":
            failures.append(f"/{slug}: status {got} Location {location!r}, expected a redirect to exactly /{slug}/")

    # explorables/404.html, not the server's built-in page: its title and stylesheet, and
    # none of the inline <style> only the built-in page has.
    _, body = check("/this-page-does-not-exist/", status=404, content_type="text/html")
    text = body.decode("utf-8", "replace")
    if "<title>Not found</title>" not in text or 'href="/index.css"' not in text or re.search(r"<style", text, re.I):
        failures.append("404 response is not explorables/404.html (no title or stylesheet, or an inline <style>)")

    if failures:
        print(f"Smoke test FAILED against {base} ({len(failures)} problem(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"Smoke test passed against {base}: landing page, stylesheet, {len(found)} explorable(s),")
    print("README paths and the 404 page, all with every header from server/explorables.sws.toml.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
