#!/usr/bin/env python3
"""Check infra/ offline: formatting, validity, and the plan tests.

Runs in CI (the `infra` job in .github/workflows/hugo.yml) and at pre-push when infra/
changed (.pre-commit-config.yaml). First two static rules: the deploy workflows only use
registry commands the deployer's role allows, and Cloudflare records are held to two
files: the production records (the live site at kphutt.com and www, and email) only in
infra/dns.tf, with every value written out, and the test name's records only under the test
name. Then the same four commands in both places:

    tofu fmt -check -recursive
    tofu init -backend=false -input=false
    tofu validate
    tofu test

None of them touches Google Cloud: the backend is skipped and the tests mock the
providers. Applying infra is done by hand (infra/README.md), never by a check.

The commands run in a temporary copy of the git-tracked files of infra/, never in infra/
itself. OpenTofu auto-loads terraform.tfvars and *.auto.tfvars, and the operator's git-ignored
copy holds live settings, so in place the offline tests would run against live values (and fmt
would judge an untracked file). CI has no such files; this makes every machine look like CI.
"""

from __future__ import annotations

import ast
import ipaddress
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INFRA = REPO / "infra"


def pinned() -> str:
    m = re.search(r'^opentofu\s*=\s*"([^"]+)"', (REPO / "mise.toml").read_text(encoding="utf-8"), re.M)
    if not m:
        sys.exit("no opentofu pin in mise.toml")
    return m.group(1)


def tofu() -> list[str]:
    # CI installs the pinned version on PATH; a dev machine gets it through mise.
    # The checks run in a temp dir outside the repo, where mise finds no mise.toml, so
    # resolve the pinned binary from the repo and hand back its path.
    if shutil.which("mise"):
        found = subprocess.run(["mise", "which", "tofu"], capture_output=True, text=True, cwd=REPO)
        if found.returncode != 0 or not found.stdout.strip():
            sys.exit(f"mise cannot find tofu (run `mise install`): {found.stderr.strip()}")
        return [found.stdout.strip()]
    if shutil.which("tofu"):
        return ["tofu"]
    sys.exit("neither mise nor tofu is on PATH -- see mise.toml")


def snapshot(dest: Path) -> Path:
    """Copy the git-tracked files of infra/ into dest/infra and return that directory."""
    out = subprocess.run(["git", "ls-files", "-z", "--", "infra"], capture_output=True, cwd=REPO)
    if out.returncode != 0:
        sys.exit(f"git ls-files failed: {out.stderr.decode(errors='replace')}")
    names = [n for n in out.stdout.decode("utf-8").split("\0") if n]
    if not names:
        sys.exit("git ls-files found no tracked files under infra/")
    for name in names:
        if not (REPO / name).exists():
            sys.exit(f"{name} is tracked by git but missing from the working tree "
                     "(deleted? stage the deletion)")
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / name, target)
    return dest / "infra"


# The deployer holds roles/artifactregistry.writer (infra/identities.tf): it can push and
# create or move tags, but not delete tags or images. `gcloud artifacts docker tags add`
# on an existing tag deletes and recreates it, so it fails for this identity after the
# first deploy -- once traffic has already moved. These are the registry commands that
# need a permission the deployer does not have.
NEEDS_DELETE = re.compile(
    r"artifacts\s+docker\s+tags\s+(?:add|delete)|artifacts\s+tags\s+delete"
    r"|artifacts\s+(?:docker\s+images|versions|packages)\s+delete"
)
DEPLOY_FILES = [".github/workflows/hugo.yml", ".github/workflows/rollback.yml", "scripts/move_live_tag.py"]


def check_deployer_commands() -> bool:
    ok = True
    for name in DEPLOY_FILES:
        text = (REPO / name).read_text(encoding="utf-8")
        if name.endswith(".py"):
            # Skip the module docstring, which explains the command it avoids. Python
            # code passes commands as lists, so join its string items into one line.
            doc = ast.get_docstring(ast.parse(text), clean=False) or ""
            text = text.replace(doc, "", 1)
            text = re.sub(r'",\s*"', " ", text)
        for n, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if NEEDS_DELETE.search(line):
                print(f"{name}:{n}: needs a delete permission the deployer does not have: {line.strip()}")
                ok = False
    return ok


# kphutt.com and www.kphutt.com serve the live site. Cloudflare records live in two places,
# and each is held to its own rule.
#
# infra/dns.tf (PRODUCTION_FILE) holds the zone's production records, adopted by import
# blocks, as two resources (PRODUCTION below), each over its own fixed list of one-line
# entries with every value literal, and each with its own import block:
#
#   - cloudflare_dns_record.web over local.web_records: the apex and www (A, AAAA, CNAME);
#   - cloudflare_dns_record.mail over local.mail_records: MX and TXT.
#
# It is the only file that may import a Cloudflare object, the only file that may use those
# lists, and its records are DNS only and carry prevent_destroy. So a change to the live site's
# DNS is always a changed line in that file. No entry may be the test name or under it: those
# belong to test-domain.tf. Nothing may loosen these pins: dns.tf holds no ignore_changes and
# no replace_triggered_by, and no moved or removed block may be in dns.tf or name the two
# resources anywhere else.
#
# Every other Cloudflare resource is held to this:
#
#   - the only Cloudflare resource type is a DNS record, and there is no Cloudflare data
#     source or import block (nothing existing is read into, or adopted by, this state);
#   - a record's name is one of the expressions below, never a literal. Two are under the
#     test name, whose validation refuses the apex and www; the third is kphutt.com's
#     ownership record (site-domain.tf), one checked label directly under the zone, so it
#     can never be the apex, www, a mail name or the test name;
#   - every record is DNS only and carries the precondition that refuses the apex and www;
#   - the ownership record is a CNAME. The test name's records come only from the fixed lists
#     local.test_address_records (A and AAAA records holding literal addresses) and
#     local.test_subdomain_records (one CNAME to ghs.googlehosted.com), and they wait for the
#     mapping, whose postcondition checks Google asked for exactly that list.
#
# A literal name, "@", a new expression, a new record type or a new resource type fails here,
# so widening what OpenTofu manages at Cloudflare takes a change to this rule, in review.
CLOUDFLARE_TYPES = {"cloudflare_dns_record"}
RECORD_NAMES = {"var.test_hostname", "local.verification_record_name", "local.apex_verification_record_name"}
# The lines a record must carry, by its name.
RECORD_RULES = {
    "local.verification_record_name": {
        "type": '"CNAME"',
    },
    "local.apex_verification_record_name": {
        "type": '"CNAME"',
        "content": "local.apex_verification_target",
        "prevent_destroy": "true",
    },
    "var.test_hostname": {
        "for_each": "local.test_domain ? local.test_records : {}",
        "type": "each.value.type",
        "content": "each.value.content",
        "depends_on": "[google_cloud_run_domain_mapping.test]",
    },
}
TEST_RECORDS = "test_records = local.verify ? local.test_address_records : local.test_subdomain_records"
# The types each fixed list may hold, and what a value of that type must be.
RECORD_LISTS = {
    "test_address_records": {
        "A": lambda v: isinstance(ipaddress.ip_address(v), ipaddress.IPv4Address),
        "AAAA": lambda v: isinstance(ipaddress.ip_address(v), ipaddress.IPv6Address),
    },
    "test_subdomain_records": {
        "CNAME": lambda v: v == "ghs.googlehosted.com",
    },
}
ENTRY = re.compile(r'^\s*"([a-z0-9-]+)"\s*=\s*\{\s*type\s*=\s*"([^"]*)"\s*,\s*content\s*=\s*"([^"]*)"\s*\}\s*$')
# local.verification_record_name must be built under the test name in both branches.
VERIFICATION_NAME_PARTS = (
    'endswith(local.verification_label, ".${var.test_hostname}")',
    '"${local.verification_label}.${var.test_hostname}"',
)
# local.apex_verification_record_name must be one checked label directly under the zone. These
# lines are required exactly, and the record must carry the precondition that checks the label.
APEX_VERIFICATION_LINES = {
    "apex_verification_record_name": '"${local.apex_verification_label}.${local.zone_name}"',
    "apex_verification_label_ok": 'can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", '
                                  'local.apex_verification_label)) && local.apex_verification_label != "www" '
                                  '&& local.apex_verification_record_name != var.test_hostname',
}
APEX_VERIFICATION_PRECONDITION = "local.apex_verification_label_ok"


def _ipv4(v: str) -> bool:
    return isinstance(ipaddress.ip_address(v), ipaddress.IPv4Address)


def _ipv6(v: str) -> bool:
    return isinstance(ipaddress.ip_address(v), ipaddress.IPv6Address)


# Cloud Run domain mappings. The test name's is in test-domain.tf. The live names' are in
# site-domain.tf (SITE_DOMAIN_FILE) and nowhere else. Each is named by the expression below,
# carries prevent_destroy (once DNS points at a mapping, deleting it takes its name off the
# internet) and a postcondition that compares Google's request with its fixed list, and each
# list holds only the kind of record Google asks for that name.
SITE_DOMAIN_FILE = "site-domain.tf"
ZONE_NAME = '"kphutt.com"'
MAPPINGS = {
    "test": {"file": "test-domain.tf", "name": "var.test_hostname"},
    "apex": {"file": SITE_DOMAIN_FILE, "name": "local.zone_name", "list": "apex_mapping_records",
             "allowed": {"A": _ipv4, "AAAA": _ipv6}},
    "www": {"file": SITE_DOMAIN_FILE, "name": '"www.${local.zone_name}"', "list": "www_mapping_records",
            "allowed": {"CNAME": lambda v: v == "ghs.googlehosted.com"}},
}
PRODUCTION_FILE = "dns.tf"
ZONE = "kphutt.com"
# The default test_hostname (variables.tf). Production entries stay off it and out from under it.
TEST_NAME = "next.kphutt.com"
# prevent_destroy on the web records (the apex and www). It must match dns.tf. Both stay "true"
# except during a rollback, which deletes the four AAAA records, so its pull request sets both
# to "false" first (docs/design/cutover-runbook.md).
# The mail records need no such constant: their prevent_destroy is always "true".
WEB_PREVENT_DESTROY = "true"
# The two production resources: the list each one runs over, the record types that list may
# hold, the names it may hold (None: any name in the zone), its prevent_destroy, and whether
# an entry may be a record that does not exist yet (id = null, so its import is skipped and
# the apply creates it). Only the web list may: the cutover adds the apex's new records
# before Cloudflare has given them ids, and the pull request after the apply writes the ids
# in. Every mail record exists and is adopted.
PRODUCTION = {
    "web": {"list": "web_records", "types": {"A", "AAAA", "CNAME"},
            "names": {ZONE, "www." + ZONE}, "prevent_destroy": WEB_PREVENT_DESTROY, "new_records": True},
    "mail": {"list": "mail_records", "types": {"MX", "TXT"},
             "names": None, "prevent_destroy": "true", "new_records": False},
}


def production_rules(resource: str) -> dict[str, str]:
    """The lines cloudflare_dns_record.<resource> must carry, exactly once each."""
    return {
        "for_each": f"local.{PRODUCTION[resource]['list']}",
        "zone_id": "var.cloudflare_zone_id",
        "name": "each.value.name",
        "type": "each.value.type",
        "content": "each.value.content",
        "priority": "each.value.priority",
        "ttl": "1",
        "proxied": "false",
        "prevent_destroy": PRODUCTION[resource]["prevent_destroy"],
    }


def production_import(resource: str) -> dict[str, str]:
    """The lines the import block for cloudflare_dns_record.<resource> must carry."""
    lst = f"local.{PRODUCTION[resource]['list']}"
    if PRODUCTION[resource]["new_records"]:
        lst = f"{{ for k, v in {lst} : k => v if v.id != null }}"
    return {
        "for_each": f"var.import_production_dns ? {lst} : {{}}",
        "to": f"cloudflare_dns_record.{resource}[each.key]",
        "id": '"${var.cloudflare_zone_id}/${each.value.id}"',
    }


# Lifecycle arguments that would let a plan drift from, or replace, a pinned record without a
# changed line in a record list. cloud-run.tf has a reviewed ignore_changes of its own; these
# are refused in dns.tf only.
LOOSENERS = re.compile(r"\b(ignore_changes|replace_triggered_by)\b")
# A moved block re-homes a record in state, and a removed block drops it from state, so either
# would take a record out from under its pins without a plan ever deleting it.
STATE_BLOCK = re.compile(r"^\s*(moved|removed)\s*\{", re.M)
PRODUCTION_ENTRY = re.compile(
    r'^\s*"([a-z0-9-]+)"\s*=\s*\{\s*id\s*=\s*("[0-9a-f]{32}"|null)\s*,\s*name\s*=\s*"([^"]+)"\s*,'
    r'\s*type\s*=\s*"([A-Z]+)"\s*,\s*content\s*=\s*"((?:[^"\\]|\\")*)"\s*,\s*priority\s*=\s*(null|[0-9]+)\s*\}\s*$'
)
HOST = re.compile(r"^([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9])?[.])+[a-z]{2,}$")


def production_value_ok(rtype: str, content: str, priority: str) -> bool:
    """A literal value of a type the production list may hold. Only MX has a priority."""
    if (rtype == "MX") != (priority != "null"):
        return False
    if rtype == "A":
        return isinstance(ipaddress.ip_address(content), ipaddress.IPv4Address)
    if rtype == "AAAA":
        return isinstance(ipaddress.ip_address(content), ipaddress.IPv6Address)
    if rtype in ("CNAME", "MX"):
        return bool(HOST.match(content))
    if rtype == "TXT":
        # Cloudflare stores TXT values quoted; "${" would be an expression, not a literal.
        return content.startswith('\\"') and content.endswith('\\"') and "${" not in content
    return False


def check_production_list(texts: dict[str, str], resource: str) -> list[str]:
    """local.<list>: defined once, in dns.tf, as one literal line per record of its kind."""
    rule = PRODUCTION[resource]
    lst = rule["list"]
    problems = []
    for name, text in texts.items():
        if name != PRODUCTION_FILE and re.search(rf"\b{lst}\b", text):
            problems.append(f"{name}: local.{lst} may only be used in {PRODUCTION_FILE}")
    text = texts.get(PRODUCTION_FILE, "")
    m = re.search(rf"^\s*{lst}\s*=\s*\{{", text, re.M)
    if not m:
        return problems + [f"{PRODUCTION_FILE}: local.{lst} is missing"]
    if len(re.findall(rf"^\s*{lst}\s*=", text, re.M)) != 1:
        return problems + [f"{PRODUCTION_FILE}: local.{lst} is defined more than once"]
    seen = 0
    for line in block_body(text, m.end()).splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        e = PRODUCTION_ENTRY.match(line)
        if not e:
            problems.append(f"local.{lst}: not a one-line {{ id, name, type, content, priority }} "
                            f"entry: {line.strip()[:80]}")
            continue
        key, rid, rname, rtype, content, priority = e.groups()
        seen += 1
        if rid == "null" and not rule["new_records"]:
            problems.append(f"local.{lst}[{key}]: id is null, but every record in this list must "
                            "exist and be adopted")
        if not (rname == ZONE or rname.endswith("." + ZONE)):
            problems.append(f"local.{lst}[{key}]: {rname} is not in the {ZONE} zone")
        if rname == TEST_NAME or rname.endswith("." + TEST_NAME):
            problems.append(f"local.{lst}[{key}]: {rname} belongs to test-domain.tf, not here")
        if rtype not in rule["types"]:
            problems.append(f"local.{lst}[{key}]: a {rtype} record does not belong in this list "
                            f"(only {sorted(rule['types'])})")
        if rule["names"] is not None and rname not in rule["names"]:
            problems.append(f"local.{lst}[{key}]: {rname} does not belong in this list "
                            f"(only {sorted(rule['names'])})")
        try:
            good = production_value_ok(rtype, content, priority)
        except ValueError:
            good = False
        if not good:
            problems.append(f"local.{lst}[{key}]: {rtype} {content[:40]!r} "
                            f"priority {priority} is not allowed")
    if not seen:
        problems.append(f"local.{lst} is empty")
    return problems


def check_production_loosening(texts: dict[str, str]) -> list[str]:
    """Nothing may defeat the production pins: no lifecycle escape hatch, no state surgery."""
    problems = []
    for name, text in texts.items():
        # Drop comments, which explain these words. No string in infra/ holds a "#".
        code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
        if name == PRODUCTION_FILE:
            problems += [f"{name}: {m.group(1)} is not allowed here (it would loosen the production pins)"
                         for m in LOOSENERS.finditer(code)]
        for m in STATE_BLOCK.finditer(code):
            body = block_body(code, m.end())
            if name == PRODUCTION_FILE or re.search(
                    rf"cloudflare_dns_record\.({'|'.join(PRODUCTION)})\b", body):
                problems.append(f"{name}: a {m.group(1)} block may not touch the production records")
    return problems


def check_exact_lines(where: str, body: str, rules: dict[str, str]) -> list[str]:
    return [f"{where}: {key} must be {want}, got {attribute(body, key)}"
            for key, want in rules.items() if attribute(body, key) != [want]]


BLOCK = re.compile(r'^\s*(resource|data)\s+"([^"]+)"\s+"([^"]+)"\s*\{', re.M)
IMPORT = re.compile(r"^\s*import\s*\{", re.M)


def block_body(text: str, start: int) -> str:
    """The text from text[start] up to the brace that closes the one before it."""
    depth, i = 1, start
    while depth and i < len(text):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[start:i - 1]


def attribute(body: str, key: str) -> list[str]:
    return re.findall(rf"^\s*{key}\s*=\s*(.+?)\s*$", body, re.M)


def check_record_list(every: str, name: str, allowed: dict) -> list[str]:
    """Every entry of the fixed list `name` must be one line of an allowed type and value."""
    m = re.search(rf"^\s*{name}\s*=\s*\{{", every, re.M)
    if not m:
        return [f"local.{name} is missing"]
    if len(re.findall(rf"^\s*{name}\s*=", every, re.M)) != 1:
        return [f"local.{name} is defined more than once"]
    problems, seen = [], 0
    for line in block_body(every, m.end()).splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        e = ENTRY.match(line)
        if not e:
            problems.append(f"local.{name}: not a one-line {{ type, content }} entry: {line.strip()}")
            continue
        _, rtype, value = e.groups()
        try:
            good = rtype in allowed and allowed[rtype](value)
        except ValueError:
            good = False
        if not good:
            problems.append(f"local.{name}: {rtype} {value!r} is not allowed (only {sorted(allowed)})")
        seen += 1
    if not seen:
        problems.append(f"local.{name} is empty")
    return problems


def check_mappings(texts: dict[str, str]) -> list[str]:
    """Every domain mapping is one of MAPPINGS, in its file, held to its rule."""
    problems = []
    seen = dict.fromkeys(MAPPINGS, 0)
    for name, text in texts.items():
        for m in BLOCK.finditer(text):
            kind, rtype, rname = m.groups()
            if "domain_mapping" not in rtype:
                continue
            where = f"{name}: {kind} {rtype}.{rname}"
            rule = MAPPINGS.get(rname)
            if kind != "resource" or rtype != "google_cloud_run_domain_mapping" or rule is None:
                problems.append(f"{where}: the only domain mappings are "
                                + ", ".join(f"google_cloud_run_domain_mapping.{r}" for r in MAPPINGS))
                continue
            if name != rule["file"]:
                problems.append(f"{where}: belongs in {rule['file']}")
                continue
            seen[rname] += 1
            body = block_body(text, m.end())
            if attribute(body, "name") != [rule["name"]]:
                problems.append(f"{where}: name must be {rule['name']}, got {attribute(body, 'name')}")
            if "list" in rule:
                problems += check_exact_lines(where, body, {"prevent_destroy": "true"})
                expected = rule["list"].replace("_records", "_expected")
                if not re.search(rf"==\s*local\.{expected}\s*,?\s*$", body, re.M) \
                        or f"length(local.{rule['list']})" not in body:
                    problems.append(f"{where}: missing the postcondition that compares Google's request "
                                    f"with local.{rule['list']}")
    for rname, rule in MAPPINGS.items():
        if seen[rname] != 1:
            problems.append(f"{rule['file']}: must hold google_cloud_run_domain_mapping.{rname} "
                            f"exactly once, found {seen[rname]}")
        if "list" not in rule:
            continue
        text = texts.get(rule["file"], "")
        problems += [f"{rule['file']}: {p}" for p in check_record_list(text, rule["list"], rule["allowed"])]
        expected = rule["list"].replace("_records", "_expected")
        want = f'toset([for r in values(local.{rule["list"]}) : "${{r.type}} ${{lower(r.content)}}"])'
        if attribute(text, expected) != [want]:
            problems.append(f"{rule['file']}: local.{expected} must be defined once, as: {want}")
        for other, other_text in texts.items():
            if other != rule["file"] and re.search(rf"\b({rule['list']}|{expected})\b", other_text):
                problems.append(f"{other}: local.{rule['list']} may only be used in {rule['file']}")
    zone_names = [v for t in texts.values() for v in attribute(t, "zone_name")]
    if zone_names != [ZONE_NAME]:
        problems.append(f"local.zone_name must be defined once, as {ZONE_NAME}, got {zone_names}")
    return problems


# Each ownership check lives in the file of the name it proves, once.
SITE_VERIFICATIONS = {"apex": SITE_DOMAIN_FILE, "test": "test-domain.tf"}


def check_site_verifications(texts: dict[str, str]) -> list[str]:
    """Every google_site_verification_web_resource is one of SITE_VERIFICATIONS, in its file, once."""
    problems = []
    seen = dict.fromkeys(SITE_VERIFICATIONS, 0)
    for name, text in texts.items():
        for m in BLOCK.finditer(text):
            kind, rtype, rname = m.groups()
            if rtype != "google_site_verification_web_resource":
                continue
            where = f"{name}: {kind} {rtype}.{rname}"
            want = SITE_VERIFICATIONS.get(rname)
            if kind != "resource" or want is None:
                problems.append(f"{where}: the only ownership checks are "
                                + ", ".join(f"google_site_verification_web_resource.{r}" for r in SITE_VERIFICATIONS))
            elif name != want:
                problems.append(f"{where}: belongs in {want}")
            else:
                seen[rname] += 1
    problems += [f"{SITE_VERIFICATIONS[r]}: must hold google_site_verification_web_resource.{r} exactly once, found {n}"
                 for r, n in seen.items() if n != 1]
    return problems


def check_cloudflare(infra: Path = INFRA) -> bool:
    problems = []
    problems += [f"{f.name}: JSON configuration is not covered by this rule; write HCL"
                 for f in sorted(infra.glob("*.tf.json"))]
    texts = {f.name: f.read_text(encoding="utf-8") for f in sorted(infra.glob("*.tf"))}
    production = dict.fromkeys(PRODUCTION, 0)
    imports = dict.fromkeys(PRODUCTION, 0)
    for name, text in texts.items():
        for m in IMPORT.finditer(text):
            body = block_body(text, m.end())
            if name == PRODUCTION_FILE:
                targets = re.findall(r"cloudflare_dns_record\.(\w+)\[", body)
                if len(targets) != 1 or targets[0] not in PRODUCTION:
                    problems.append(f"{name}: an import block must adopt one of "
                                    f"{sorted(PRODUCTION)}, got {targets}")
                    continue
                imports[targets[0]] += 1
                problems += check_exact_lines(f"{name}: import", body, production_import(targets[0]))
            elif "cloudflare_" in body:
                problems.append(f"{name}: an import block adopts an existing Cloudflare object "
                                f"(only {PRODUCTION_FILE} may)")
        for m in BLOCK.finditer(text):
            kind, rtype, rname = m.groups()
            where = f"{name}: {kind} {rtype}.{rname}"
            if name == PRODUCTION_FILE:
                # dns.tf holds the production records and nothing else.
                if kind != "resource" or rtype != "cloudflare_dns_record" or rname not in PRODUCTION:
                    problems.append(f"{where}: {PRODUCTION_FILE} may hold only the resources "
                                    + ", ".join(f"cloudflare_dns_record.{r}" for r in PRODUCTION))
                    continue
                production[rname] += 1
                problems += check_exact_lines(where, block_body(text, m.end()), production_rules(rname))
                continue
            if not rtype.startswith("cloudflare_"):
                continue
            if kind == "data":
                problems.append(f"{where}: no Cloudflare data sources (nothing existing is read)")
                continue
            if rtype not in CLOUDFLARE_TYPES:
                problems.append(f"{where}: only {', '.join(sorted(CLOUDFLARE_TYPES))} is allowed")
                continue
            body = block_body(text, m.end())
            names = attribute(body, "name")
            if len(names) != 1 or names[0] not in RECORD_NAMES:
                problems.append(f"{where}: name must be one of {sorted(RECORD_NAMES)}, got {names}")
            else:
                for key, want in RECORD_RULES[names[0]].items():
                    got = attribute(body, key)
                    if got != [want]:
                        problems.append(f"{where}: {key} must be {want}, got {got}")
            if not re.search(r"^\s*proxied\s*=\s*false\s*$", body, re.M):
                problems.append(f"{where}: must set proxied = false")
            if "lookup(local.allowed_record_name," not in body:
                problems.append(f"{where}: missing the precondition that refuses kphutt.com and www")
            if names == ["local.apex_verification_record_name"]:
                if name != SITE_DOMAIN_FILE:
                    problems.append(f"{where}: kphutt.com's ownership record belongs in {SITE_DOMAIN_FILE}")
                if APEX_VERIFICATION_PRECONDITION not in body:
                    problems.append(f"{where}: missing the precondition that checks the token's label "
                                    f"({APEX_VERIFICATION_PRECONDITION})")
    for resource in PRODUCTION:
        if production[resource] != 1:
            problems.append(f"{PRODUCTION_FILE}: must hold resource cloudflare_dns_record.{resource} "
                            f"exactly once, found {production[resource]}")
        if imports[resource] != 1:
            problems.append(f"{PRODUCTION_FILE}: must hold exactly one import block for "
                            f"cloudflare_dns_record.{resource}, found {imports[resource]}")
        problems += check_production_list(texts, resource)
    problems += check_production_loosening(texts)
    every = "\n".join(texts.values())
    if "var.test_hostname" in every:
        if len(re.findall(rf"^\s*{re.escape(TEST_RECORDS)}\s*$", every, re.M)) != 1 or len(re.findall(r"^\s*test_records\s*=", every, re.M)) != 1:
            problems.append(f"local.test_records must be defined once, as: {TEST_RECORDS}")
        for name, allowed in RECORD_LISTS.items():
            problems += check_record_list(every, name, allowed)
    if "local.apex_verification_record_name" in every:
        for key, want in APEX_VERIFICATION_LINES.items():
            if attribute(every, key) != [want]:
                problems.append(f"local.{key} must be defined once, as: {want}")
    problems += check_mappings(texts)
    problems += check_site_verifications(texts)
    if "local.verification_record_name" in every:
        problems += [f"local.verification_record_name is no longer built under the test name (missing {part})"
                     for part in VERIFICATION_NAME_PARTS if part not in every]
    for p in problems:
        print(f"cloudflare guard: {p}")
    return not problems


def main() -> int:
    if not check_deployer_commands():
        print("\ninfra check FAILED: a deploy step uses a registry command the deployer cannot run", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix="check-infra-") as tmp:
        infra = snapshot(Path(tmp))
        return run_infra(infra)


def run_infra(infra: Path) -> int:
    if not check_cloudflare(infra):
        print("\ninfra check FAILED: a Cloudflare record breaks the production or test-name rule",
              file=sys.stderr)
        return 1
    cmd = tofu()
    version = subprocess.run([*cmd, "version"], capture_output=True, text=True, cwd=infra).stdout
    want = pinned()
    if f"OpenTofu v{want}" not in version:
        sys.exit(f"OpenTofu is not the pinned {want}:\n{version}")
    for step in (
        ["fmt", "-check", "-recursive"],
        ["init", "-backend=false", "-input=false", "-no-color"],
        ["validate", "-no-color"],
        ["test", "-no-color"],
    ):
        print(f"\n$ tofu {' '.join(step)}", flush=True)
        if subprocess.run([*cmd, *step], cwd=infra).returncode != 0:
            print(f"\ninfra check FAILED at: tofu {' '.join(step)}", file=sys.stderr)
            return 1
    print("\ninfra check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
