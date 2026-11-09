#!/usr/bin/env python3
"""Check infra/ offline: formatting, validity, and the plan tests.

Runs in CI (the `infra` job in .github/workflows/hugo.yml) and at pre-push when infra/
changed (.pre-commit-config.yaml). First it checks the deploy workflows only use registry
commands the deployer's role allows. Then the same four commands in both places:

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


def main() -> int:
    if not check_deployer_commands():
        print("\ninfra check FAILED: a deploy step uses a registry command the deployer cannot run", file=sys.stderr)
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
