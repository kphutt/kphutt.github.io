#!/usr/bin/env python3
"""Report what a change did to the built site, by comparing two build directories.

This is a REPORT, not a gate. It never fails. scripts/check_build.py decides whether a
build is shippable; this one answers the different question of "what actually moved",
which a pass/fail check cannot tell you.

It exists because of a regression that every existing check missed. A theme update moved
its typography rules from .post-content onto a new .md-content class. The homepage is a
local override that emitted only the old class, so it lost every paragraph and heading
margin. The HTML of that page was byte-identical before and after -- the CSS had moved out
from under it -- so no HTML diff would have shown anything. The build was clean, the markup
valid, and the page visibly wrong. It was caught by eye, which is not a control.

The orphaned-selector section below is the answer to that: it tracks how many CSS rules
mention each class the HTML actually uses, and reports the ones that collapsed.

    python scripts/compare_builds.py BASE_BUILD HEAD_BUILD [--title "..."]
"""

import argparse
import re
import sys
from pathlib import Path

# Noise that changes on every build without meaning anything changed.
NOISE = [
    (re.compile(r"stylesheet\.[0-9a-f]{64}\.css"), "stylesheet.HASH.css"),
    (re.compile(r'integrity="sha256-[A-Za-z0-9+/=]+"'), 'integrity="SRI"'),
    (re.compile(r"Hugo [0-9]+\.[0-9]+\.[0-9]+"), "Hugo VERSION"),
    (re.compile(r"Hugo -- [0-9]+\.[0-9]+\.[0-9]+"), "Hugo -- VERSION"),
    (re.compile(r"<lastBuildDate>[^<]*</lastBuildDate>"), "<lastBuildDate>DATE</lastBuildDate>"),
]

TEXT = {".html", ".xml", ".txt"}


def denoise(text: str) -> str:
    for pattern, replacement in NOISE:
        text = pattern.sub(replacement, text)
    return text


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def text_files(root: Path):
    return {
        p.relative_to(root).as_posix(): p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in TEXT
    }


def class_sets(root: Path) -> set:
    """Every distinct class ATTRIBUTE in the built HTML, as a sorted tuple.

    Deliberately per-element rather than per-class. A theme that moves rules from
    .post-content onto .md-content and emits both leaves the element styled exactly as
    before, even though .post-content lost almost all of its rules. Counting per class
    calls that a regression; counting per element correctly calls it a no-op -- and still
    catches the real case, where the element carries only the old class.
    """
    found = set()
    for path in root.rglob("*.html"):
        for attr in re.findall(r'class=(?:"([^"]*)"|\'([^\']*)\'|([^\s">]+))', read(path)):
            for chunk in attr:
                names = tuple(sorted(c for c in chunk.split() if c))
                if names:
                    found.add(names)
    return found


def css_text(root: Path) -> str:
    return "".join(read(p) for p in sorted(root.rglob("*.css")))


def rule_counts(css: str, names) -> dict:
    """How many times the CSS mentions each class as a selector."""
    return {n: len(re.findall(r"\." + re.escape(n) + r"(?![\w-])", css)) for n in names}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("head")
    ap.add_argument("--title", default="Build comparison")
    args = ap.parse_args()

    base, head = Path(args.base), Path(args.head)
    for d in (base, head):
        if not d.is_dir():
            print(f"### {args.title}\n\nBuild directory not found: `{d}`")
            return 0

    out = [f"### {args.title}", ""]

    # --- pages added and removed -------------------------------------------------
    bf, hf = text_files(base), text_files(head)
    added, removed = sorted(set(hf) - set(bf)), sorted(set(bf) - set(hf))
    if added or removed:
        out.append("**Files**")
        for f in added:
            out.append(f"- added `{f}`")
        for f in removed:
            out.append(f"- removed `{f}`")
        out.append("")

    # --- which shared pages changed, ignoring build noise -------------------------
    changed = []
    for name in sorted(set(bf) & set(hf)):
        if denoise(read(bf[name])) != denoise(read(hf[name])):
            b, h = len(read(bf[name])), len(read(hf[name]))
            changed.append((name, h - b))
    if changed:
        out.append("**Content changed** (build noise ignored: css hash, SRI, Hugo version, RSS date)")
        out.append("")
        out.append("| file | size delta |")
        out.append("|---|---|")
        for name, delta in changed:
            out.append(f"| `{name}` | {delta:+d} bytes |")
        out.append("")
    elif not added and not removed:
        out.append("No change to any page once build noise is ignored.")
        out.append("")

    # --- the section that earns this script's existence ---------------------------
    base_css, head_css = css_text(base), css_text(head)
    base_sets, head_sets = class_sets(base), class_sets(head)
    all_names = {n for s in base_sets | head_sets for n in s}
    before, after = rule_counts(base_css, all_names), rule_counts(head_css, all_names)

    def total(names, counts):
        return sum(counts.get(n, 0) for n in names)

    # Only a LOSS counts. An element that was never styled by a class selector -- because
    # it is targeted by an element or descendant rule instead, like `header` or an svg
    # icon -- scores zero in both builds and is not a regression. Reporting those buries
    # the real signal, and a report that cries wolf stops being read.
    lost = []
    for names in sorted(head_sets):
        if names not in base_sets:
            continue
        was, now = total(names, before), total(names, after)
        if was > 0 and now < was / 2:
            why = "not styled by anything" if now == 0 else "most rules gone"
            lost.append((names, was, now, why))

    if lost:
        out.append("**Styling** -- elements whose CSS went away")
        out.append("")
        out.append("| element classes | rules before | rules after | |")
        out.append("|---|---|---|---|")
        for names, was, now, why in lost[:10]:
            shown = " ".join(names)
            out.append(f"| `{shown}` | {was if was is not None else 'n/a'} | **{now}** | {why} |")
        out.append("")
        out.append("Counted per element, not per class, so a rename that adds the new class")
        out.append("alongside the old one is correctly ignored. An element listed here renders")
        out.append("unstyled -- and its HTML can be byte-identical to before.")
        out.append("")
    else:
        out.append("**Styling** -- every element in the HTML is still styled.")
        out.append("")

    css_delta = len(head_css) - len(base_css)
    if css_delta:
        out.append(f"CSS size {css_delta:+d} bytes.")
        out.append("")

    # --- a few values worth eyeballing -------------------------------------------
    def facts(root: Path) -> dict:
        index = root / "index.html"
        if not index.is_file():
            return {}
        html = read(index)

        def grab(pattern, default="(absent)"):
            m = re.search(pattern, html)
            return m.group(1) if m else default

        return {
            "canonical": grab(r"<link rel=canonical href=(\S+?)>"),
            "description": str(len(grab(r'<meta name=description content="([^"]*)"', ""))) + " chars",
            "twitter:description": str(len(grab(r'twitter:description content="([^"]*)"', ""))) + " chars",
            "CSP hashes": str(len(re.findall(r"'sha256-[A-Za-z0-9+/=]+'", html))),
            "inline scripts": str(len([1 for a, b in re.findall(r"<script([^>]*)>(.*?)</script>", html, re.S) if "ld+json" not in a and b.strip()])),
        }

    fb, fh = facts(base), facts(head)
    drift = [(k, fb.get(k), fh.get(k)) for k in fh if fb.get(k) != fh.get(k)]
    if drift:
        out.append("**Homepage values that moved**")
        out.append("")
        out.append("| | before | after |")
        out.append("|---|---|---|")
        for k, b, h in drift:
            out.append(f"| {k} | `{b}` | `{h}` |")
        out.append("")

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
