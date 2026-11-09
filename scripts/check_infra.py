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
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
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
    if shutil.which("mise"):
        return ["mise", "exec", "--", "tofu"]
    if shutil.which("tofu"):
        return ["tofu"]
    sys.exit("neither mise nor tofu is on PATH -- see mise.toml")


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
#   - every record is DNS only and carries the precondition that refuses the apex and www.
#
# A literal name, "@", a new expression or a new resource type fails here, so widening what
# OpenTofu manages at Cloudflare takes a change to this rule, in review.
CLOUDFLARE_TYPES = {"cloudflare_dns_record"}
RECORD_NAMES = {"var.test_hostname", "local.verification_record_name"}
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
            names = re.findall(r"^\s*name\s*=\s*(.+?)\s*$", body, re.M)
            if len(names) != 1 or names[0] not in RECORD_NAMES:
                problems.append(f"{where}: name must be one of {sorted(RECORD_NAMES)}, got {names}")
            if not re.search(r"^\s*proxied\s*=\s*false\s*$", body, re.M):
                problems.append(f"{where}: must set proxied = false")
            if "lookup(local.allowed_record_name," not in body:
                problems.append(f"{where}: missing the precondition that refuses kphutt.com and www")
    every = "\n".join(texts.values())
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
    if not check_cloudflare():
        print("\ninfra check FAILED: a Cloudflare resource could touch kphutt.com or www", file=sys.stderr)
        return 1
    cmd = tofu()
    version = subprocess.run([*cmd, "version"], capture_output=True, text=True, cwd=INFRA).stdout
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
        if subprocess.run([*cmd, *step], cwd=INFRA).returncode != 0:
            print(f"\ninfra check FAILED at: tofu {' '.join(step)}", file=sys.stderr)
            return 1
    print("\ninfra check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
