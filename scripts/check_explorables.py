#!/usr/bin/env python3
"""Check the built explorables before they can be deployed.

Explorables are standalone interactive pages served from explorables.kphutt.com, their own
origin, so the main site keeps script-src 'none' and its own gate (scripts/check_build.py)
untouched. Here a page may load its own script files. Everything else is held to the same
rules as the site, and this file reuses check_build.py's checks to apply them:

  - no inline code: no inline <script>, no <style> element, no style= or on...= attribute,
    no javascript: URL;
  - nothing loaded from another origin, by any page, stylesheet or script module;
  - every page carries <meta name="robots" content="noindex, nofollow">;
  - server/explorables.sws.toml sends the full header set, X-Robots-Tag included, with a
    Content-Security-Policy no weaker than POLICY_FLOOR on any directive and no connect-src;
  - every explorables/<slug>/ has an index.html and a README.md, and no README is in the
    output, where the server would serve it;
  - no dot-files in the output;
  - explorables.Dockerfile builds on exactly the image the site's Dockerfile pins.

Run it against the build directory:

    python scripts/build_explorables.py
    python scripts/check_explorables.py public-explorables
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_build as cb  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "explorables"
CONFIG = REPO / "server" / "explorables.sws.toml"
DOCKERFILES = (REPO / "Dockerfile", REPO / "explorables.Dockerfile")

# The explorables origin. Its host name is the domain mapping in infra/explorables.tf.
ORIGIN = "https://explorables.kphutt.com"

# The weakest policy explorables may ship, read the same way as check_build.POLICY_FLOOR:
# each directive maps to the only sources it may hold, and a directive not listed may only
# be 'none'. So script-src gaining 'unsafe-inline', a hash, a nonce, another origin or a
# wildcard fails, and so does default-src 'self'.
POLICY_FLOOR = {
    "default-src": {"'none'"},
    "script-src": {"'self'"},
    "style-src": {"'self'"},
    "img-src": {"'self'"},
    "font-src": {"'self'"},
    "object-src": {"'none'"},
    "base-uri": {"'none'"},
    "form-action": {"'none'"},
    "frame-ancestors": {"'none'"},
}
# Every directive in the floor must be written out, and Trusted Types too.
# require-trusted-types-for is a restrict-only directive in check_build, so its value is
# checked below.
REQUIRED_DIRECTIVES = (*POLICY_FLOOR, "require-trusted-types-for")

REQUIRED_HEADERS = {
    "X-Content-Type-Options": {"nosniff"},
    "Referrer-Policy": {"no-referrer"},
    "Cross-Origin-Opener-Policy": {"same-origin"},
    "Cross-Origin-Resource-Policy": {"same-origin"},
    "X-Robots-Tag": {"noindex, nofollow"},
}

# Files the output must have, and why.
EXPECTED_FILES = {
    "index.html": "the landing page would 404",
    "404.html": "missing pages would get the server's built-in page, whose inline <style> the CSP blocks",
}

SLUG = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

ATTR = re.compile(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")


def attributes(tag: str) -> dict:
    return {m.group(1).lower(): next(v for v in m.groups()[1:] if v is not None) for m in ATTR.finditer(tag)}


def check_server(failures: list, config: Path = CONFIG, site_config: Path = cb.SERVER_CONFIG) -> None:
    headers = cb.check_server_headers(failures, config, POLICY_FLOOR, REQUIRED_DIRECTIVES, REQUIRED_HEADERS)
    name = config.relative_to(config.parent.parent).as_posix()
    if headers is None:
        return
    policy = cb.parse_policy(headers.get("content-security-policy") or "")
    # default-src 'none' blocks fetch, XHR, WebSocket and beacons. Even connect-src 'none'
    # is refused, so the rule reads "no connect-src" and nothing has to be argued.
    if "connect-src" in policy:
        failures.append(f"{name}: Content-Security-Policy has connect-src -- explorables make no network calls")
    if [s.lower() for s in policy.get("require-trusted-types-for", [])] != ["'script'"]:
        failures.append(f"{name}: Content-Security-Policy must have require-trusted-types-for 'script'")
    site = cb.check_server_headers([], site_config) or {}
    if headers.get("permissions-policy") != site.get("permissions-policy"):
        failures.append(f"{name}: Permissions-Policy must equal the site's in {site_config.name}")
    if not headers.get("cache-control"):
        failures.append(f"{name}: no Cache-Control header on the catch-all rule")

    config_text = config.read_text(encoding="utf-8")
    general = cb.tomllib.loads(config_text).get("general", {})
    for key, want in (("page404", "404.html"), ("directory-listing", False), ("ignore-hidden-files", True)):
        if general.get(key) != want:
            failures.append(f"{name}: {key} must be {want!r}, got {general.get(key)!r}")


def check_pages(root: Path, failures: list) -> None:
    cb.check_inline_code(root, failures, allow_external_scripts=True)
    cb.check_no_outside_loads(root, ORIGIN, failures, allow_font_face=True)

    for path in sorted(root.rglob("*.html")):
        html = path.read_text(encoding="utf-8", errors="replace")
        page = path.relative_to(root).as_posix()

        robots = [attributes(t) for t in re.findall(r"<meta\b[^>]*>", html, re.I)]
        robots = [a for a in robots if a.get("name", "").lower() == "robots"]
        tokens = {t.strip().lower() for a in robots for t in a.get("content", "").split(",")}
        if not {"noindex", "nofollow"} <= tokens:
            failures.append(f'{page}: no <meta name="robots" content="noindex, nofollow">')

        # A <script> the closing-tag pattern in check_inline_code cannot see would swallow
        # the rest of the page, and whatever it holds would be missed.
        if len(re.findall(r"<script\b", html, re.I)) != len(re.findall(r"<script\b[^>]*>.*?</script\s*>", html, re.S | re.I)):
            failures.append(f"{page}: a <script> without its </script>")
        for tag in re.findall(r"<script\b[^>]*>", html, re.I):
            src = attributes(tag).get("src")
            if src is not None:
                check_same_origin(page, src, failures)

        for ref in re.findall(r'(?:href|src)=["\']?([^"\'>\s]+)', html):
            target = cb.resolve_ref(ref, ORIGIN, root, path)
            if target is not None and not target.exists():
                failures.append(f"{page}: broken internal reference {ref}")

    # Script modules can load other modules. Each specifier must stay on this origin.
    imports = re.compile(
        r"""(?:\bimport\s*\(\s*|\bimport\s+(?:[^'";]*?\s+from\s+)?|\bexport\s+[^'";]*?\s+from\s+)["']([^"']+)["']"""
    )
    for path in sorted([*root.rglob("*.js"), *root.rglob("*.mjs")]):
        page = path.relative_to(root).as_posix()
        for spec in imports.findall(path.read_text(encoding="utf-8", errors="replace")):
            check_same_origin(page, spec, failures)


def check_same_origin(page: str, url: str, failures: list) -> None:
    url = url.strip()
    if url.startswith("//") or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url):
        failures.append(f"{page}: loads script {url} from another origin, or by absolute URL -- use a relative one")


def check_layout(source: Path, root: Path, failures: list) -> None:
    for name, why in EXPECTED_FILES.items():
        if not (root / name).is_file():
            failures.append(f"{name} missing from the output -- {why}")
    if source.is_dir():
        for folder in sorted(p for p in source.iterdir() if p.is_dir()):
            where = f"explorables/{folder.name}/"
            if not SLUG.match(folder.name):
                failures.append(f"{where}: not a slug (lowercase letters, digits and hyphens)")
            if not (folder / "README.md").is_file():
                failures.append(f"{where}: no README.md saying what the page is and where its parts came from")
            if not (folder / "index.html").is_file():
                failures.append(f"{where}: no index.html")
    else:
        failures.append(f"{source} not found")
    served = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.name.lower().startswith("readme"))
    if served:
        failures.append(f"a README is in the output, where the server would serve it: {', '.join(served)}")
    hidden = cb.hidden_files(root)
    if hidden:
        failures.append(f"hidden files in the output: {', '.join(sorted(hidden))}")


def check_base_image(failures: list, dockerfiles=DOCKERFILES) -> None:
    froms = [re.findall(r"^FROM\s+(\S+)", d.read_text(encoding="utf-8"), re.M) for d in dockerfiles]
    if any(len(f) != 1 for f in froms) or len({f[0] for f in froms}) != 1:
        failures.append(
            "the Dockerfiles do not build on the same pinned image: "
            + ", ".join(f"{d.name} {f}" for d, f in zip(dockerfiles, froms))
        )


def check(root: Path, source: Path = SOURCE, config: Path = CONFIG,
          site_config: Path = cb.SERVER_CONFIG, dockerfiles=DOCKERFILES) -> list:
    failures = []
    check_layout(source, root, failures)
    check_pages(root, failures)
    check_server(failures, config, site_config)
    check_base_image(failures, dockerfiles)
    return failures


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "public-explorables")
    if not root.is_dir():
        sys.exit(f"FAIL: build directory not found: {root} (run scripts/build_explorables.py)")
    failures = check(root)
    if failures:
        print(f"Explorables check FAILED ({len(failures)} problem(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1
    slugs = [p.name for p in SOURCE.iterdir() if p.is_dir()]
    print(f"Explorables check passed: {len(slugs)} explorable(s), every page noindex,")
    print("no inline code, nothing loaded from another origin, no README or dot-file served,")
    print("CSP at or above the floor in server/explorables.sws.toml with no connect-src,")
    print("every required header set, base image the same as the site's.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
