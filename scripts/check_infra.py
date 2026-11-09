#!/usr/bin/env python3
"""Check infra/ offline: formatting, validity, and the plan tests.

Runs in CI (the `infra` job in .github/workflows/hugo.yml) and at pre-push when infra/
changed (.pre-commit-config.yaml). First two static rules: the deploy workflows only use
registry commands the deployer's role allows, and no Cloudflare resource can touch
kphutt.com or www (the live site). Then the same four commands in both places:

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


# kphutt.com and www.kphutt.com serve the live site. infra/ manages only the test name's
# records (infra/test-domain.tf), so every Cloudflare resource is held to this:
#
#   - the only Cloudflare resource type is a DNS record, and there is no Cloudflare data
#     source or import block (nothing existing is read into, or adopted by, this state);
#   - a record's name is one of the expressions below, never a literal. Both are under the
#     test name, whose validation refuses the apex and www;
#   - every record is DNS only and carries the precondition that refuses the apex and www;
#   - the ownership record is a CNAME. The test name's records come only from the fixed lists
#     local.test_address_records (A and AAAA records holding literal addresses) and
#     local.test_subdomain_records (one CNAME to ghs.googlehosted.com), and they wait for the
#     mapping, whose postcondition checks Google asked for exactly that list.
#
# A literal name, "@", a new expression, a new record type or a new resource type fails here,
# so widening what OpenTofu manages at Cloudflare takes a change to this rule, in review.
CLOUDFLARE_TYPES = {"cloudflare_dns_record"}
RECORD_NAMES = {"var.test_hostname", "local.verification_record_name"}
# The lines a record must carry, by its name.
RECORD_RULES = {
    "local.verification_record_name": {
        "type": '"CNAME"',
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


def check_cloudflare(infra: Path = INFRA) -> bool:
    problems = []
    problems += [f"{f.name}: JSON configuration is not covered by this rule; write HCL"
                 for f in sorted(infra.glob("*.tf.json"))]
    texts = {f.name: f.read_text(encoding="utf-8") for f in sorted(infra.glob("*.tf"))}
    for name, text in texts.items():
        for m in IMPORT.finditer(text):
            if "cloudflare_" in block_body(text, m.end()):
                problems.append(f"{name}: an import block adopts an existing Cloudflare object")
        for m in BLOCK.finditer(text):
            kind, rtype, rname = m.groups()
            if not rtype.startswith("cloudflare_"):
                continue
            where = f"{name}: {kind} {rtype}.{rname}"
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
    every = "\n".join(texts.values())
    if "var.test_hostname" in every:
        if len(re.findall(rf"^\s*{re.escape(TEST_RECORDS)}\s*$", every, re.M)) != 1 or len(re.findall(r"^\s*test_records\s*=", every, re.M)) != 1:
            problems.append(f"local.test_records must be defined once, as: {TEST_RECORDS}")
        for name, allowed in RECORD_LISTS.items():
            problems += check_record_list(every, name, allowed)
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
        print("\ninfra check FAILED: a Cloudflare resource could touch kphutt.com or www", file=sys.stderr)
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
