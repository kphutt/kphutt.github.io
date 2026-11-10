#!/usr/bin/env python3
"""Smoke-test a running copy of the site: every URL answers, every header is right.

The deploy workflow runs this twice: against the container on the CI runner before
anything is pushed, and against the Cloud Run candidate revision before it gets any
traffic. Promotion happens only if it passes, so a revision that drops a header or a
page never serves a visitor.

    python scripts/smoke.py --local http://127.0.0.1:8080
    python scripts/smoke.py https://cand-abc123---site-xyz-uw.a.run.app

--local adds the checks that need a Host header the URL does not carry (the www-to-apex
redirect). Only a server reached directly can be asked that way: Cloud Run's front end
routes by Host, so a candidate URL cannot.

What it checks, against BASE:
  - every URL in the built sitemap returns 200, so no published page has moved;
  - /.well-known/security.txt returns 200 as text/plain; charset=utf-8;
  - the files indexing relies on (robots.txt, sitemap.xml, llms.txt, the RSS feed and
    the og-image) return 200 with a sensible content type;
  - every stylesheet, icon and preload the homepage links is a relative URL, so it loads
    from whichever host serves the page, and returns 200; the stylesheet carries the long,
    immutable cache rule;
  - a directory URL without its trailing slash redirects to exactly the same path with
    the slash, on the same host;
  - a missing page returns 404 with this site's own 404 page, not the server's built-in;
  - every one of those responses carries exactly the security headers in
    server/sws.toml. The expected values are read from that file, so the config is the
    one place they are written down.

Standard library only, so it runs anywhere Python does.
"""

from __future__ import annotations

import re
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "server" / "sws.toml"
HUGO = REPO / "hugo.toml"

# Header values that must be present on every response, read from the catch-all rule.
# Cache-Control is checked separately, because it legitimately varies by file.
SECURITY_HEADERS = (
    "Content-Security-Policy",
    "Strict-Transport-Security",
    "X-Content-Type-Options",
    "Referrer-Policy",
    "Permissions-Policy",
    "Cross-Origin-Opener-Policy",
    "Cross-Origin-Resource-Policy",
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D102 -- see a redirect, don't follow it
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def expected_headers() -> dict:
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    for rule in config["advanced"]["headers"]:
        if rule["source"] == "**":
            return {name: rule["headers"][name] for name in SECURITY_HEADERS}
    sys.exit(f"FAIL: no catch-all '**' header rule in {CONFIG}")


def site_origin() -> str:
    match = re.search(r'^\s*baseURL\s*=\s*"([^"]+)"', HUGO.read_text(encoding="utf-8"), re.M)
    return match.group(1).rstrip("/")


# Statuses worth waiting out on a fresh URL: a candidate's tagged hostname can answer 404
# or 5xx from Google's front end for a few seconds after it is created, and a cold
# instance can answer 5xx while it starts.
TRANSIENT = {404, 429, 500, 502, 503, 504}


def fetch(url: str, attempts: int = 1, host: str | None = None):
    """GET without following redirects. Returns (status, headers, body).

    With attempts > 1, connection errors and TRANSIENT statuses are retried, three seconds
    apart, and the last answer is returned.
    """
    headers = {"User-Agent": "site-smoke"}
    if host:
        headers["Host"] = host
    last = None
    for attempt in range(attempts):
        try:
            with OPENER.open(urllib.request.Request(url, headers=headers), timeout=20) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            last = (e.code, e.headers, e.read())
            if e.code not in TRANSIENT or attempt == attempts - 1:
                return last
        except (urllib.error.URLError, OSError) as e:
            last = e
        time.sleep(3)
    if isinstance(last, tuple):
        return last
    raise SystemExit(f"FAIL: {url} did not answer after {attempts} attempt(s): {last}")


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--local"]
    local = "--local" in sys.argv[1:]
    if len(args) != 1:
        sys.exit(__doc__)
    base = args[0].rstrip("/")
    origin = site_origin()
    want = expected_headers()
    failures = []

    def check(path, status=200, content_type=None, cache=None, attempts=1):
        url = base + path
        got, headers, body = fetch(url, attempts)
        if got != status:
            failures.append(f"{path}: status {got}, expected {status}")
        for name, value in want.items():
            if headers.get(name) != value:
                failures.append(f"{path}: {name} is {headers.get(name)!r}, expected {value!r}")
        if content_type and not (headers.get("Content-Type") or "").startswith(content_type):
            failures.append(f"{path}: Content-Type {headers.get('Content-Type')!r}, expected {content_type}")
        if cache and headers.get("Cache-Control") != cache:
            failures.append(f"{path}: Cache-Control {headers.get('Cache-Control')!r}, expected {cache!r}")
        return headers, body

    # The first request may land on a cold instance, or on a tagged URL still being set up,
    # so it gets about thirty seconds of retries.
    _, home = check("/", content_type="text/html", cache="public, max-age=300", attempts=10)

    # Every page the sitemap promises must still be where it was.
    _, sitemap = check("/sitemap.xml", content_type="application/xml")
    pages = re.findall(r"<loc>([^<]+)</loc>", sitemap.decode("utf-8", "replace"))
    if not pages:
        failures.append("sitemap.xml lists no pages")
    for loc in pages:
        if not loc.startswith(origin + "/"):
            failures.append(f"sitemap lists {loc}, which is not under {origin}")
            continue
        check(urlsplit(loc).path, content_type="text/html")

    check("/robots.txt", content_type="text/plain")
    check("/llms.txt", content_type="text/plain")
    # RFC 9116's location. The server serves dot-files only so this can be reached, so also
    # check that a dot-path that does not exist is a 404.
    _, sec = check("/.well-known/security.txt", content_type="text/plain; charset=utf-8")
    if b"Contact:" not in sec or b"Expires:" not in sec:
        failures.append("/.well-known/security.txt has no Contact or Expires field")
    check("/.git/config", status=404)
    check("/index.xml", content_type="application/xml")
    check("/og-image.png", content_type="image/png", cache="public, max-age=86400")
    check("/favicon.ico", cache="public, max-age=86400")

    # Follow what the homepage actually links, rather than paths assumed here. Each load
    # must be relative: an absolute https://kphutt.com/ URL is another origin on a test
    # host or a candidate URL, and the policy blocks it there.
    html = home.decode("utf-8", "replace")
    loads = []
    for tag in re.findall(r"<link\b[^>]*>", html, re.I):
        rel = re.search(r"""rel=["']?([^"'>]+?)["']?(?:\s|>|/)""", tag, re.I)
        if not rel or not set(rel.group(1).lower().split()) & {"stylesheet", "icon", "apple-touch-icon", "mask-icon", "preload"}:
            continue
        href = re.search(r"""href=["']?([^"'>\s]+)""", tag, re.I)
        if href:
            loads.append(href.group(1))
    if not loads:
        failures.append("homepage links no stylesheet or icon")
    for href in loads:
        if not href.startswith("/") or href.startswith("//"):
            failures.append(f"homepage loads {href}, which is not a relative URL on this host")
            continue
        if re.fullmatch(r"/assets/css/stylesheet\.[0-9a-f]+\.css", href):
            check(href, content_type="text/css", cache="public, max-age=31536000, immutable")
        else:
            check(href)
    if not any(re.fullmatch(r"/assets/css/stylesheet\.[0-9a-f]+\.css", h) for h in loads):
        failures.append("homepage links no fingerprinted stylesheet")

    # A directory without its slash redirects to the one with it, as the site always has: exactly
    # /posts/, relative, so it stays on whichever host was asked.
    got, headers, _ = fetch(base + "/posts")
    location = headers.get("Location", "")
    if got not in (301, 308) or location != "/posts/":
        failures.append(f"/posts: status {got} Location {location!r}, expected a redirect to exactly /posts/")
    for name, value in want.items():
        if headers.get(name) != value:
            failures.append(f"/posts redirect: {name} is {headers.get(name)!r}, expected {value!r}")

    # A missing page is a real 404, with the site's own 404 page and every header. The
    # server's built-in error page also says 404, so look for what only this site's page
    # has -- its stylesheet and title -- and for the inline <style> only the built-in has.
    _, body = check("/this-page-does-not-exist/", status=404, content_type="text/html")
    text = body.decode("utf-8", "replace")
    title = re.search(r'^\s*title\s*=\s*"([^"]+)"', HUGO.read_text(encoding="utf-8"), re.M).group(1)
    if not re.search(r"/assets/css/stylesheet\.[0-9a-f]+\.css", text) or title not in text or re.search(r"<style", text, re.I):
        failures.append("404 response is not this site's 404 page (no site stylesheet or title, or an inline <style>)")

    if local:
        # www redirects to the apex, path kept (server/sws.toml).
        apex = urlsplit(origin).hostname
        got, headers, _ = fetch(base + "/posts/brochure/", host="www." + apex)
        location = headers.get("Location", "")
        if got != 301 or location != f"{origin}/posts/brochure/":
            failures.append(f"www.{apex}/posts/brochure/: status {got} Location {location!r}, expected 301 to {origin}/posts/brochure/")

    if failures:
        print(f"Smoke test FAILED against {base} ({len(failures)} problem(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"Smoke test passed against {base}: {len(pages)} sitemap pages, feeds, robots.txt,")
    print(f"llms.txt, og-image, {len(loads)} linked stylesheet/icon loads, trailing-slash redirect")
    print("and 404" + (", www redirect" if local else "") + ", all with every security header from server/sws.toml.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
