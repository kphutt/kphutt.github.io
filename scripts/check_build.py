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

import base64
import hashlib
import re
import sys
from pathlib import Path

# Read the expected origin out of hugo.toml rather than hardcoding it, so this file does
# not become a second place the site's address has to be kept up to date.
CONFIG = Path(__file__).resolve().parent.parent / "hugo.toml"

TEXT_SUFFIXES = {".html", ".xml", ".txt", ".json", ".css", ".js"}

# Files the build is expected to publish, each with why it matters. All of these come from
# configuration rather than from a file in content/, so losing one is silent.
EXPECTED_FILES = {
    "robots.txt": "crawler policy, including the AI training opt-out, would go unstated",
    "llms.txt": "the plain-text index for language models would be gone",
    "sitemap.xml": "robots.txt points crawlers at it; a dead pointer is worse than none",
    "index.xml": "the RSS feed anyone subscribed to would 404",
}


def expected_base_url() -> str:
    text = CONFIG.read_text(encoding="utf-8")
    match = re.search(r'^\s*baseURL\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        sys.exit(f"FAIL: no baseURL found in {CONFIG}")
    return match.group(1).rstrip("/")


def csp_hash(body: str) -> str:
    """The CSP hash for an inline script or style: sha256 of its exact bytes, base64."""
    digest = hashlib.sha256(body.encode("utf-8")).digest()
    return "sha256-" + base64.b64encode(digest).decode("ascii")


def check_csp(root: Path, failures: list) -> None:
    """Every inline script and style must be pinned by hash in the page's own CSP.

    The policy in layouts/partials/extend_head.html allows inline code by hash rather than
    with 'unsafe-inline', which is the only way the policy is worth having. The cost is
    that a hash pins exact bytes: a theme update that changes one character of the footer
    script leaves a policy that no longer matches, the browser silently refuses to run it,
    and nothing in the build complains.

    So recompute the hashes from what was actually built and require each one to appear.
    A silent breakage becomes a failed build.
    """
    for path in sorted(root.rglob("*.html")):
        html = path.read_text(encoding="utf-8", errors="replace")
        page = path.relative_to(root).as_posix()

        inline_scripts = [
            body for attrs, body in re.findall(r"<script([^>]*)>(.*?)</script>", html, re.S)
            # JSON-LD is data, not executed, so script-src does not apply to it.
            if "ld+json" not in attrs and body.strip()
        ]
        inline_styles = [
            body for body in re.findall(r"<style[^>]*>(.*?)</style>", html, re.S) if body.strip()
        ]
        if not inline_scripts and not inline_styles:
            continue  # e.g. Hugo's pagination alias stubs

        # The policy value itself contains single quotes ('none', 'sha256-...'), so the
        # capture must stop only at the closing double quote. Hugo minifies attributes and
        # leaves http-equiv unquoted, hence the optional quotes around that name.
        match = re.search(
            r'<meta[^>]*http-equiv=["\']?Content-Security-Policy["\']?[^>]*?content="([^"]+)"',
            html,
            re.I,
        )
        if not match:
            failures.append(f"{page} has inline code but no Content-Security-Policy")
            continue
        policy = match.group(1)

        if "unsafe-inline" in policy:
            failures.append(
                f"{page} CSP contains 'unsafe-inline', which defeats hashing the inline code"
            )

        for label, bodies in (("script-src", inline_scripts), ("style-src", inline_styles)):
            directive = re.search(rf"{label}([^;]*)", policy)
            allowed = directive.group(1) if directive else ""
            for body in bodies:
                digest = csp_hash(body)
                if digest not in allowed:
                    snippet = " ".join(body.split())[:60]
                    failures.append(
                        f"{page}: inline {label.split('-')[0]} not allowed by CSP -- "
                        f"add '{digest}' to {label} (starts: {snippet})"
                    )


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

    # 4. Files the site is supposed to publish must actually be published.
    #
    # Every one of these is produced by configuration rather than by a source file, which
    # is the failure this repo keeps hitting: the config reads correctly, Hugo reports no
    # error, and the file simply is not there. robots.txt went missing exactly this way --
    # enableRobotsTXT was set and the template existed, but declaring outputs.home had
    # replaced the defaults that "robots" normally comes from.
    #
    # Nothing links these files, so the reference check below cannot catch them.
    for name, why in EXPECTED_FILES.items():
        if not (root / name).is_file():
            failures.append(f"{name} missing from build output -- {why}")

    # 5. Every internal reference must resolve to something in the build output.
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

    # 6. Inline code must be pinned by hash in the Content Security Policy.
    check_csp(root, failures)

    if failures:
        print(f"Build check FAILED ({len(failures)} problem(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"Build check passed: all URLs use {base}, canonical correct, CNAME intact,")
    print(f"{len(EXPECTED_FILES)} expected files present, internal references all resolve,")
    print("inline code pinned by hash in the CSP.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
