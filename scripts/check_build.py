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

import hashlib
import json
import re
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Read the expected origin out of hugo.toml rather than hardcoding it, so this file does
# not become a second place the site's address has to be kept up to date.
CONFIG = Path(__file__).resolve().parent.parent / "hugo.toml"

TEXT_SUFFIXES = {".html", ".xml", ".txt", ".json", ".css", ".js"}

# Files the build is expected to publish, each with why it matters. All of these come from
# configuration rather than from a file in content/, so losing one is silent.
EXPECTED_FILES = {
    "robots.txt": "the crawler policy and the sitemap pointer would be gone",
    "llms.txt": "the plain-text index for language models would be gone",
    "sitemap.xml": "robots.txt points crawlers at it; a dead pointer is worse than none",
    "index.xml": "the RSS feed anyone subscribed to would 404",
    # Referenced only from og:image and twitter:image, which are content= attributes, so
    # the internal-reference check below cannot see it. Losing it means every link preview
    # silently falls back to a bare URL.
    "og-image.png": "link previews would lose their image",
    # Nothing links it. IndexNow engines fetch it to check the key scripts/indexnow.py sends.
    "indexnow.txt": "IndexNow engines would reject every submission",
    # Linked from extend_head.html, so the reference check does cover it -- but it is
    # listed here too because losing it degrades silently to the raster fallback rather
    # than visibly breaking, which is exactly the kind of quiet regression that hides.
    "favicon.svg": "the sharp icon would silently fall back to the resampled raster",
    # server/sws.toml serves it for every missing page. Without it the server falls back
    # to a built-in page whose inline <style> the Content Security Policy blocks.
    "404.html": "missing pages would get the server's built-in error page instead",
}


def expected_base_url() -> str:
    text = CONFIG.read_text(encoding="utf-8")
    match = re.search(r'^\s*baseURL\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        sys.exit(f"FAIL: no baseURL found in {CONFIG}")
    return match.group(1).rstrip("/")


# robots.txt on the production origin (baseURL in hugo.toml). connect-src may name this
# and nothing else.
ROBOTS_URL = expected_base_url() + "/robots.txt"

# The weakest Content Security Policy the site may ship. Each directive maps to the only
# source expressions it may contain; a directive absent from the policy falls back to
# default-src, which must be 'none'. The live policy may be stricter than this, never
# looser, so loosening it -- 'unsafe-inline', a hash, a nonce, another origin, a wildcard,
# a scheme like https: or data:, or default-src 'self' -- fails the build.
POLICY_FLOOR = {
    "default-src": {"'none'"},
    "script-src": {"'none'"},
    "style-src": {"'self'"},
    "img-src": {"'self'"},
    # One exact file, so Lighthouse can fetch robots.txt from the page (server/sws.toml says
    # why). Compared as a whole source expression: 'self', the bare origin, a trailing
    # slash (which would match a whole path prefix), a wildcard or another scheme all fail.
    "connect-src": {"'none'", ROBOTS_URL},
    "object-src": {"'none'"},
    "base-uri": {"'none'"},
    "form-action": {"'none'"},
    "frame-ancestors": {"'none'"},
}

# Directives that must be present explicitly. default-src does not cover these two, so
# leaving them out would allow anything; script-src is listed so the intent is visible in
# the policy itself rather than inherited.
REQUIRED_DIRECTIVES = ("default-src", "script-src", "base-uri", "form-action")

# The response headers the server sends, from its config. These are what a browser
# enforces. The header is the only CSP: the pages carry no in-page copy.
SERVER_CONFIG = Path(__file__).resolve().parent.parent / "server" / "sws.toml"

# Headers the catch-all rule must set, and the values each may take.
REQUIRED_SERVER_HEADERS = {
    "X-Content-Type-Options": {"nosniff"},
    "Referrer-Policy": {"strict-origin-when-cross-origin", "same-origin", "no-referrer", "strict-origin"},
    "Cross-Origin-Opener-Policy": {"same-origin"},
    "Cross-Origin-Resource-Policy": {"same-origin"},
}

# One year. HSTS shorter than this protects little and is what scanners flag.
MIN_HSTS_MAX_AGE = 31536000

# Theme templates this repo overrides with a modified copy, and the sha256 of the theme
# file each copy was made from. A theme update that changes one of them fails the build,
# so a fix upstream is never silently shadowed by a stale copy here. To re-sync: diff the
# theme file against the copy, carry the change over, and update the hash.
THEME = Path(__file__).resolve().parent.parent / "themes" / "PaperMod" / "layouts"
OVERRIDDEN_THEME_FILES = {
    "_partials/head.html": "c7ef58590d6e4c755f1d2a575668095f3539475c61b39fdc12fd6e30025d30b0",
    "_partials/footer.html": "94921247defd67d205aaa2358d96365664e75de4a30d44b0ed36c6635c738543",
    "_partials/templates/schema_json.html": "3fd05ccc156d8eda928c923776b53044097600c9c31a4812e3ebcfe9196b2ffa",
}


# Directives that only ever restrict, so any value is acceptable. Every other directive
# not in POLICY_FLOOR may only be 'none': an unlisted fetch directive such as
# script-src-elem, connect-src or font-src at 'self' would loosen the default-src 'none'
# fallback (script-src-elem 'self' even overrides script-src 'none' for <script>).
# report-uri and report-to are deliberately not here: they send violation reports to a URL,
# which is a request to wherever the policy names. They fall under the 'none'-only rule
# like any other unlisted directive, so adding a reporting endpoint is a decision, not a
# drive-by.
RESTRICT_ONLY = {"sandbox", "upgrade-insecure-requests", "require-trusted-types-for", "trusted-types"}


def parse_policy(policy: str) -> dict:
    """Split a CSP string into {directive: [sources]} the way a browser does.

    Browsers split on ASCII whitespace only, lowercase directive names, and keep the
    first of a repeated directive. Python's str.split() would also split on a no-break
    space, so a policy a browser reads as one unknown directive would look valid here;
    hence the explicit separator.
    """
    parsed = {}
    for part in policy.split(";"):
        tokens = [tok for tok in re.split(r"[\t\n\f\r ]+", part) if tok]
        if tokens:
            parsed.setdefault(tokens[0].lower(), tokens[1:])
    return parsed


def policy_problems(policy: str, required=REQUIRED_DIRECTIVES, floor=POLICY_FLOOR) -> list:
    """Every way `policy` is weaker than `floor`. An empty list means it holds.

    The floor defaults to this site's; a test passes its own to check the rules.
    """
    problems = []
    if not policy.isascii() or any(ord(c) < 32 and c not in "\t\n\f\r" for c in policy):
        problems.append("contains non-ASCII or control characters, which browsers do not treat as separators")
    parsed = parse_policy(policy)
    for name in required:
        if name not in parsed:
            problems.append(f"{name} is missing")
    for name, sources in parsed.items():
        if name in RESTRICT_ONLY:
            continue
        allowed = floor.get(name, {"'none'"})
        extra = [s for s in sources if s.lower() not in allowed]
        if extra:
            problems.append(f"{name} allows {' '.join(extra)} (the floor is {' '.join(sorted(allowed))})")
        if not sources:
            problems.append(f"{name} has no value")
    return problems


# A JSON-LD block is data and never runs. Recognised by its exact type attribute, not by
# the text "ld+json" appearing anywhere among the attributes.
JSON_LD = re.compile(r"""(?:^|\s)type\s*=\s*["']?application/ld\+json["']?(?:\s|$)""", re.I)

# Any type attribute. A browser reads only the first of two, so a second one could hide a
# script typed as JavaScript behind a JSON-LD type later in the tag. An attribute can
# follow whitespace, a quote or a slash.
TYPE_ATTR = re.compile(r"""(?:^|[\s"'/])type\s*=""", re.I)


def json_ld_problem(attrs: str, body: str):
    """Why a <script type="application/ld+json"> block is not plain data, or None.

    A data block is never run, so the policy has nothing to allow for it. That only holds
    for exactly what search engines read: one type attribute, no src, and a body that is a
    JSON object or array. "<!--" and "<script" inside a script element change where the
    browser ends it, so neither may appear. Templates write these blocks with jsonify,
    which escapes "<", so a real one never contains either.
    """
    if len(TYPE_ATTR.findall(attrs)) != 1:
        return "has more than one type attribute"
    if SRC_ATTR.search(attrs):
        return "has a src attribute"
    if "<!--" in body or re.search(r"<script", body, re.I):
        return 'contains "<!--" or "<script", which changes where the element ends'
    try:
        data = json.loads(body)
    except ValueError as e:
        return f"is not valid JSON ({e})"
    if not isinstance(data, (dict, list)):
        return "is not a JSON object or array"
    return None


# A src attribute on its own, not data-src or similar.
SRC_ATTR = re.compile(r"""(?:^|\s)src\s*=""", re.I)


def check_inline_code(root: Path, failures: list) -> None:
    """The built site must contain no code the policy would have to allow.

    The policy is script-src 'none' with no 'unsafe-inline' and no hashes, which only
    holds if the pages contain nothing that needs them. Inline code is exactly what a
    theme update reintroduces without anyone noticing, so look for every form of it:
    inline and external scripts, <style> elements, style= attributes, event-handler
    attributes and javascript: URLs. JSON-LD is data, not executed, so it stays, as long
    as it is only data (json_ld_problem).
    """
    # SVG files are documents too: opened directly, one can carry script or handlers.
    for path in sorted([*root.rglob("*.html"), *root.rglob("*.svg")]):
        html = path.read_text(encoding="utf-8", errors="replace")
        page = path.relative_to(root).as_posix()

        for attrs, body in re.findall(r"<script\b([^>]*)>(.*?)</script\s*>", html, re.S | re.I):
            if JSON_LD.search(attrs):
                problem = json_ld_problem(attrs, body)
                if problem:
                    failures.append(f"{page}: JSON-LD block {problem}")
                continue
            kind = "external script" if "src=" in attrs.lower() else "inline script"
            snippet = " ".join((attrs + " " + body).split())[:60]
            failures.append(f"{page}: {kind}, which script-src 'none' blocks (starts: {snippet})")
        if re.search(r"<style[\s>]", html, re.I):
            failures.append(f"{page}: inline <style> element -- move the CSS into a stylesheet file")
        # An attribute can follow whitespace, a slash or a closing quote (<svg/onload=,
        # <div class="a"style=), and may have spaces around its =.
        if re.search(r"""<[^>]+[\s/"']style\s*=""", html, re.I):
            failures.append(f"{page}: inline style= attribute -- move it into a stylesheet file")
        handler = re.search(r"""<[^>]+[\s/"'](on[a-z]+)\s*=""", html, re.I)
        if handler:
            failures.append(f"{page}: event-handler attribute {handler.group(1)}= is inline script")
        if re.search(r"""(?:href|src|action|formaction|data)\s*=\s*["']?\s*javascript:""", html, re.I):
            failures.append(f"{page}: javascript: URL is inline script")


def check_no_outside_loads(root: Path, base: str, failures: list) -> None:
    """Nothing the browser fetches by itself may come from another origin.

    Outbound <a> links are fine -- the visitor clicks them, the page does not load them.
    Stylesheets, icons, preloads, images and CSS url()s are what the page loads, and the
    policy only allows this site for those. Catch it here rather than as a silent console
    error on the live site.
    """
    loads = []
    for path in sorted(root.rglob("*.html")):
        html = path.read_text(encoding="utf-8", errors="replace")
        page = path.relative_to(root).as_posix()
        for tag in re.findall(r"<link\b[^>]*>", html, re.I):
            rel = re.search(r"""rel=["']?([^"'>]+?)["']?(?:\s|>|/)""", tag, re.I)
            rel = rel.group(1).lower() if rel else ""
            # alternate/canonical/etc are metadata, not loads.
            if not any(r in rel.split() for r in ("stylesheet", "icon", "apple-touch-icon", "mask-icon", "preload", "prefetch", "manifest", "modulepreload")):
                continue
            href = re.search(r"""href=["']?([^"'>\s]+)""", tag, re.I)
            if href:
                loads.append((page, href.group(1)))
        for src in re.findall(r"""<(?:img|source|iframe|embed|video|audio|track|input)\b[^>]*\ssrc\s*=\s*["']?([^"'>\s]+)""", html, re.I):
            loads.append((page, src))
        for url in re.findall(r"""<video\b[^>]*\sposter\s*=\s*["']?([^"'>\s]+)""", html, re.I):
            loads.append((page, url))
        for url in re.findall(r"""<object\b[^>]*\sdata\s*=\s*["']?([^"'>\s]+)""", html, re.I):
            loads.append((page, url))
        # srcset holds a comma-separated list of "URL width" candidates.
        for srcset in re.findall(r"""\ssrcset\s*=\s*["']([^"']+)["']""", html, re.I):
            for candidate in srcset.split(","):
                if candidate.strip():
                    loads.append((page, candidate.split()[0]))
    for path in sorted(root.rglob("*.css")):
        css = path.read_text(encoding="utf-8", errors="replace")
        page = path.relative_to(root).as_posix()
        if re.search(r"@import|@font-face", css, re.I):
            failures.append(f"{page}: @import or @font-face -- the policy has no font-src and loads no other sheet")
        for url in re.findall(r"url\(\s*['\"]?([^'\")]+)", css, re.I):
            loads.append((page, url))
    for page, url in loads:
        if url.startswith("data:"):
            failures.append(f"{page}: data: URL load {url[:40]} -- the policy does not allow data:")
        elif url.startswith("//") or (re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url) and not url.startswith(base + "/")):
            failures.append(f"{page}: loads {url} from another origin")
        elif url.startswith(base + "/"):
            # Same site, but pinned to its production host name. On any other host (a test
            # subdomain, a deploy candidate's own URL) that is another origin, so the
            # policy blocks it there. Canonical and og: links are metadata and stay absolute.
            failures.append(f"{page}: loads {url} by absolute URL -- use a relative one (relURL)")


def check_server_headers(
    failures: list,
    config_path: Path = SERVER_CONFIG,
    floor: dict = POLICY_FLOOR,
    required_directives: tuple = REQUIRED_DIRECTIVES + ("frame-ancestors", "object-src"),
    required_headers: dict = REQUIRED_SERVER_HEADERS,
    refused_hsts_flags: tuple = ("includesubdomains", "preload"),
):
    """The server config must send the full header set, with a policy at or above the floor.

    This is the policy the site is served with. It covers frame-ancestors and applies to
    every response, including feeds, images and the 404 page. Loosening it below the floor
    fails the build.

    The defaults are this site's; the parameters let a test check the rules on its own
    config. Returns the catch-all rule's headers, lowercased, so a caller
    can check more of them, or None if there is no catch-all rule to read.
    """
    name = config_path.relative_to(config_path.parent.parent).as_posix()
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        failures.append(f"{name} could not be read: {e}")
        return None

    general = config.get("general", {})
    # The server's built-in versions of these would add HSTS with includeSubDomains and
    # preload, and a one-year cache on files whose names never change.
    for switch in ("security-headers", "cache-control-headers"):
        if general.get(switch) is not False:
            failures.append(f"{name}: {switch} must be false -- the explicit headers replace it")

    rules = (config.get("advanced") or {}).get("headers") or []
    catch_all = [r for r in rules if r.get("source") == "**"]
    if not catch_all:
        failures.append(f"{name}: no catch-all [[advanced.headers]] rule with source = \"**\"")
        return None
    # HTTP header names are case-insensitive but TOML keys are not, so a second,
    # differently-cased copy of a header could slip a weaker value past these checks.
    # Compare lowercased, and refuse duplicates outright.
    def lowered(rule):
        out = {}
        for key, value in rule.get("headers", {}).items():
            if key.lower() in out:
                failures.append(f"{name}: rule for {rule.get('source')!r} sets {key} twice (names differ only in case)")
            out[key.lower()] = value
        return out

    headers = lowered(catch_all[0])

    # A later rule replaces an earlier value, so only Cache-Control and Content-Type may be
    # set by the narrower rules. Anything else there could silently weaken the catch-all.
    for rule in rules:
        if rule is catch_all[0]:
            continue
        for header in lowered(rule):
            if header not in ("cache-control", "content-type"):
                failures.append(
                    f"{name}: rule for {rule.get('source')!r} sets {header}, which would "
                    f"override the catch-all for those files"
                )

    policy = headers.get("content-security-policy")
    if not policy:
        failures.append(f"{name}: no Content-Security-Policy header")
    else:
        for problem in policy_problems(policy, required_directives, floor):
            failures.append(f"{name}: Content-Security-Policy is weaker than the floor: {problem}")

    hsts = headers.get("strict-transport-security", "")
    max_age = re.search(r"max-age=(\d+)", hsts)
    if not max_age or int(max_age.group(1)) < MIN_HSTS_MAX_AGE:
        failures.append(f"{name}: Strict-Transport-Security max-age must be at least {MIN_HSTS_MAX_AGE}")
    # Both are hard to undo and cover every subdomain, including ones served elsewhere.
    # Adding either is a decision for the site's owner, so it fails here until this check
    # is changed deliberately alongside it.
    for flag in refused_hsts_flags:
        if flag in hsts.lower():
            failures.append(f"{name}: Strict-Transport-Security has {flag}, which needs an explicit decision")

    for header, allowed in required_headers.items():
        if headers.get(header.lower()) not in allowed:
            failures.append(f"{name}: {header} is {headers.get(header.lower())!r}, expected one of {sorted(allowed)}")
    if not headers.get("permissions-policy"):
        failures.append(f"{name}: no Permissions-Policy header")
    return headers


# Server features neither site uses, and the value that turns each off. Each one reads the
# request, so off is less code an attacker's request can reach. Each must be written out,
# not left to the default: a value in the config file overrides the binary's default and any
# SERVER_* environment variable, so nothing outside the file can turn one back on.
SERVER_FEATURES_OFF = {
    "health": False,
    "metrics": False,
    "maintenance-mode": False,
    "accept-markdown": False,
    "cors-allow-origins": "",
    "basic-auth": "",
    "page-fallback": "",
    "directory-listing": False,
    "directory-listing-download": [],
    "compression-static": False,
    "follow-symlinks": False,
    "tls": False,
    "http2": False,
    "https-redirect": False,
    "use-relative-root": False,
    "etag": False,
    "log-remote-address": False,
    "log-x-real-ip": False,
    "log-forwarded-for": False,
}

# Every other [general] key a config may hold. A key in neither list fails: v3 ignores a
# key it does not know with only a warning, so a misspelled one would silently leave its
# feature at the default, and a new key is a new feature, which is a decision.
SERVER_KEYS_ALLOWED = {
    "host", "port", "root", "log-level", "page404", "security-headers", "cache-control-headers",
    "compression", "include-hidden", "redirect-trailing-slash", "index-files", "idle-timeout",
    "text-charset",
}


def server_settings_problems(name: str, config: dict, advanced_allowed: frozenset) -> list:
    """What in a parsed server config turns on a feature the sites do not use.

    advanced_allowed is the set of [advanced] tables the config may have. The rest
    (rewrites, virtual-hosts, memory-cache, and redirects where unused) must be absent,
    which is the only way to turn them off.
    """
    problems = []
    for section in config:
        if section not in ("general", "advanced"):
            problems.append(f"{name}: [{section}] is not a section this gate knows")
    general = config.get("general") or {}
    for key, off in SERVER_FEATURES_OFF.items():
        if key not in general:
            problems.append(f"{name}: {key} must be set to {off!r}, not left to the default")
        elif general[key] != off or type(general[key]) is not type(off):
            problems.append(f"{name}: {key} is {general[key]!r}, but must be {off!r}: the site does not use it")
    for key in general:
        if key not in SERVER_FEATURES_OFF and key not in SERVER_KEYS_ALLOWED:
            problems.append(f"{name}: {key} is not a key this gate knows (misspelled, or a feature to decide on)")
    # Closes a connection that sends or reads nothing for this long (GHSA-2fch-9x9r-mxmv).
    # 0 turns it off, and leaving it out trusts the default.
    timeout = general.get("idle-timeout")
    if type(timeout) is not int or timeout < 1:
        problems.append(f"{name}: idle-timeout must be set to a whole number of seconds, at least 1, got {timeout!r}")
    for table in config.get("advanced") or {}:
        if table not in advanced_allowed:
            problems.append(f"{name}: [advanced.{table}] turns on a feature the site does not use")
    return problems


def check_server_settings(failures: list, config_path: Path = SERVER_CONFIG,
                          advanced_allowed: frozenset = frozenset({"headers", "redirects"})) -> None:
    """Every server feature the site does not use is written out as off.

    The defaults are this site's, which uses one redirect (www to the apex).
    """
    name = config_path.relative_to(config_path.parent.parent).as_posix()
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        failures.append(f"{name} could not be read: {e}")
        return
    failures.extend(server_settings_problems(name, config, advanced_allowed))


def check_theme_overrides(failures: list) -> None:
    for name, pinned in OVERRIDDEN_THEME_FILES.items():
        theme_file = THEME / name
        if not theme_file.is_file():
            failures.append(f"theme file {name} is gone, but layouts/ still overrides it")
            continue
        actual = hashlib.sha256(theme_file.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        if actual != pinned:
            failures.append(
                f"theme file {name} changed upstream (sha256 {actual}); re-sync the copy in "
                f"layouts/partials/ and update OVERRIDDEN_THEME_FILES in this script"
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


SECURITY_TXT = ".well-known/security.txt"
SECURITY_CONTACT = "https://github.com/kphutt/kphutt.github.io/security/advisories/new"


def hidden_files(root: Path, allowed=()) -> list:
    """Every dot-file or dot-folder under root, or inside one, except the paths in allowed."""
    return [
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if any(part.startswith(".") for part in p.relative_to(root).parts)
        and p.relative_to(root).as_posix() not in allowed
    ]


def check_security_txt(root: Path, base: str, failures: list) -> None:
    """RFC 9116: the file exists, has its required fields, and Expires is neither past nor
    more than a year out. Expires is generated at build time, so this catches a template
    that stops generating it; the monthly rebuild keeps it from going stale."""
    path = root / SECURITY_TXT
    if not path.is_file():
        failures.append(f"{SECURITY_TXT} missing from build output -- no way to report a vulnerability")
        return
    fields = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and ": " in line:
            name, value = line.split(": ", 1)
            fields.setdefault(name.strip(), []).append(value.strip())
    if fields.get("Contact") != [SECURITY_CONTACT]:
        failures.append(f"{SECURITY_TXT}: Contact is {fields.get('Contact')}, expected [{SECURITY_CONTACT!r}]")
    if fields.get("Canonical") != [f"{base}/{SECURITY_TXT}"]:
        failures.append(f"{SECURITY_TXT}: Canonical is {fields.get('Canonical')}, expected {base}/{SECURITY_TXT}")
    if len(fields.get("Expires", [])) != 1:
        failures.append(f"{SECURITY_TXT}: needs exactly one Expires field")
    else:
        try:
            expires = datetime.fromisoformat(fields["Expires"][0].replace("Z", "+00:00"))
            if expires.tzinfo is None:
                raise ValueError("no timezone")
        except ValueError:
            failures.append(f"{SECURITY_TXT}: Expires {fields['Expires'][0]!r} is not an ISO 8601 date with a zone")
        else:
            now = datetime.now(timezone.utc)
            if expires <= now:
                failures.append(f"{SECURITY_TXT}: Expires {expires.isoformat()} is in the past")
            elif expires > now + timedelta(days=365):
                failures.append(f"{SECURITY_TXT}: Expires {expires.isoformat()} is more than a year away (RFC 9116)")
    # server/sws.toml serves dot-files so that .well-known can be reached, so nothing else
    # hidden may ride along in the image.
    stray = hidden_files(root, allowed=(".well-known", SECURITY_TXT))
    if stray:
        failures.append(f"hidden files in build output, which the server would serve: {', '.join(sorted(stray))}")


def rel_me_profiles(config):
    """Every profile the homepage must link back with rel="me": sameAs plus relMeOnly."""
    schema = config.get("params", {}).get("schema", {})
    return list(schema.get("sameAs", [])) + list(schema.get("relMeOnly", []))


def rel_me_problems(html: str, same_as: list) -> list:
    """The profile links the page must carry as rel="me", but does not.

    Mastodon verifies a profile link only when the linked page points back with rel="me".
    layouts/partials/extend_head.html writes one per params.schema.sameAs and params.schema.relMeOnly
    entry, so the build must hold one for every entry (Hugo may leave the attributes unquoted).
    """
    found = set()
    for tag in re.findall(r"<link\b[^>]*>", html, re.I):
        rel = re.search(r"""\brel=["']?([^"'>\s]+)""", tag, re.I)
        href = re.search(r"""\bhref=["']?([^"'>\s]+)""", tag, re.I)
        if rel and href and "me" in rel.group(1).lower().split():
            found.add(href.group(1))
    return [f"no <link rel=\"me\"> for {url}" for url in same_as if url not in found]


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

    # 2b. Every sameAs and relMeOnly profile must be linked back with rel="me" (Mastodon verification).
    if index.is_file():
        profiles = rel_me_profiles(tomllib.loads(CONFIG.read_text(encoding="utf-8")))
        failures += [f"homepage: {p}" for p in rel_me_problems(html, profiles)]

    # 3. Files the site is supposed to publish must actually be published.
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

    # 5. The homepage's content wrapper must carry the same classes as a post's.
    #
    # layouts/index.html is a local override; post pages render through the theme's own
    # template. When the theme moved its typography rules from .post-content onto a new
    # .md-content class, the theme's pages picked it up and the overridden homepage did
    # not -- every paragraph and heading there silently lost its margins. The build was
    # clean and the HTML was valid; only the rendering was wrong.
    #
    # Comparing the two rather than hardcoding class names means this keeps working when
    # the theme renames something again: both sides move together, and only a genuine
    # divergence between the override and the theme fails.
    def wrapper_classes(page: Path):
        if not page.is_file():
            return None
        found = re.search(
            r'<div class=["\']?([^"\'>]*\bpost-content\b[^"\'>]*)["\']?[ >]',
            page.read_text(encoding="utf-8", errors="replace"),
        )
        return set(found.group(1).split()) if found else None

    home_classes = wrapper_classes(root / "index.html")
    post_classes = wrapper_classes(root / "posts" / "brochure" / "index.html")
    if home_classes is None:
        failures.append("homepage has no post-content wrapper -- layouts/index.html may be broken")
    elif post_classes and home_classes != post_classes:
        missing = post_classes - home_classes
        extra = home_classes - post_classes
        detail = []
        if missing:
            detail.append(f"missing {sorted(missing)}")
        if extra:
            detail.append(f"unexpected {sorted(extra)}")
        failures.append(
            "homepage content wrapper does not match the theme's: "
            + ", ".join(detail)
            + " -- layouts/index.html has drifted from the theme template"
        )

    # 6. No inline code, and nothing loaded from elsewhere.
    check_inline_code(root, failures)
    check_no_outside_loads(root, base, failures)

    # 7. The server's CSP holds the floor, and every other required header is set.
    check_server_headers(failures)
    # ...and every server feature the site does not use is off.
    check_server_settings(failures)

    # 8. security.txt is present, well formed and unexpired.
    check_security_txt(root, base, failures)

    # 9. The theme files overridden with modified copies have not moved upstream.
    check_theme_overrides(failures)

    if failures:
        print(f"Build check FAILED ({len(failures)} problem(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"Build check passed: all URLs use {base}, canonical correct,")
    print(f"{len(EXPECTED_FILES)} expected files present, internal references all resolve,")
    print("no inline code, CSP at or above the floor in server/sws.toml, unused server features off,")
    print("nothing loaded from another origin, security.txt valid, theme overrides in sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
