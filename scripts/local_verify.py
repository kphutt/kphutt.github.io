#!/usr/bin/env python3
"""Run the CI build gate locally, on the same terms CI runs it.

Dispatched by pre-commit at the pre-push stage (.pre-commit-config.yaml). The point is
that this file adds no checks of its own -- it runs the two commands from the `build` job
in .github/workflows/hugo.yml verbatim:

    hugo --gc --minify
    python3 scripts/check_build.py public

What it does add is CI's *preconditions*, which a working tree does not have for free.
Both of these were real failures, not theory:

1. The right Hugo. scripts/check_build.py compares hashes of the theme's inline scripts
   against the Content Security Policy in layouts/partials/extend_head.html. Hashes pin
   exact bytes and Hugo versions minify differently, so the wrong Hugo fails the check on
   every page while CI is green. A local 0.157.0 did exactly that. mise.toml pins the
   version; this script refuses to run on anything else rather than emit failures that
   look like site bugs.

2. A clean output directory. CI builds into a fresh checkout. A working tree accumulates:
   public/ is gitignored, Hugo does not purge files that no longer correspond to any
   content, and `hugo server` injects livereload into what it leaves behind. This tree
   held public/notes/ from March, four months after content/notes/ stopped existing, and
   the check duly failed on it. So public/ is removed before building.

Run it directly any time:  python scripts/local_verify.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUILD_DIR = REPO / "public"

# CI sets both explicitly in the build step. TZ decides how dates render, and Hugo's
# environment decides which config and minification path applies, so a local build that
# leaves them to the machine is not the build CI graded.
CI_ENV = {"HUGO_ENVIRONMENT": "production", "TZ": "America/Los_Angeles"}


def fail(msg: str) -> "typing.NoReturn":  # noqa: F821
    print(f"\nlocal gate FAILED: {msg}", file=sys.stderr)
    sys.exit(1)


def pinned_hugo_version() -> str:
    """The single source of truth for which Hugo this repo builds with."""
    text = (REPO / "mise.toml").read_text(encoding="utf-8")
    m = re.search(r'^hugo-extended\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        fail("no hugo-extended pin found in mise.toml")
    return m.group(1)


def hugo_command() -> list[str]:
    """Prefer the mise-pinned Hugo; fall back to PATH only if it happens to match."""
    if shutil.which("mise"):
        return ["mise", "exec", "--", "hugo"]
    if shutil.which("hugo"):
        return ["hugo"]
    fail("neither mise nor hugo found on PATH -- see mise.toml for install instructions")


def check_version(hugo: list[str], want: str) -> None:
    out = subprocess.run(
        [*hugo, "version"], capture_output=True, text=True, cwd=REPO
    ).stdout
    m = re.search(r"hugo v(\S+?)[-+]", out)
    got = m.group(1) if m else "unknown"
    if got != want:
        fail(
            f"Hugo {got} is not the pinned {want}.\n"
            f"  The CSP hash check compares exact bytes, so the wrong version reports\n"
            f"  failures on pages you never touched. Install the pin with: mise install"
        )
    print(f"hugo {got} (pinned)")


def run(cmd: list[str], label: str) -> None:
    print(f"\n$ {' '.join(cmd)}")
    env = {**os.environ, **CI_ENV}
    if subprocess.run(cmd, cwd=REPO, env=env).returncode != 0:
        fail(label)


def main() -> int:
    hugo = hugo_command()
    check_version(hugo, pinned_hugo_version())

    # CI builds into a fresh checkout. Reproduce that precondition.
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
        print(f"removed stale {BUILD_DIR.name}/")

    run([*hugo, "--gc", "--minify"], "hugo build failed")
    run([sys.executable, "scripts/check_build.py", "public"], "build check failed")

    print("\nlocal gate passed -- matches the CI build job")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
