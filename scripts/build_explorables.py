#!/usr/bin/env python3
"""Copy explorables/ into the directory the explorables image serves, without its READMEs.

    python scripts/build_explorables.py [OUT]     # OUT defaults to public-explorables/

Every README.md is for the repository and is never served, so it is left out here. Nothing
else is changed or dropped: a stray file is copied as it is, and scripts/check_explorables.py
fails on it, rather than vanishing quietly. OUT is emptied first, so a file deleted from
explorables/ cannot outlive it in a working tree (the same reason scripts/local_verify.py
empties public/).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "explorables"
DEFAULT_OUT = REPO / "public-explorables"


def is_readme(name: str) -> bool:
    return name.lower() == "readme.md"


def build(source: Path = SOURCE, out: Path = DEFAULT_OUT) -> None:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(source, out, ignore=lambda _dir, names: [n for n in names if is_readme(n)])


def main() -> int:
    out = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_OUT
    if not SOURCE.is_dir():
        sys.exit(f"FAIL: {SOURCE} not found")
    build(SOURCE, out)
    print(f"Built {out.name}/ from explorables/ (READMEs left out)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
