#!/usr/bin/env python3
"""Plan, check and apply this repository's OpenTofu infrastructure in one command.

    python scripts/apply_infra.py --plan-only          # plan and check it, apply nothing
    python scripts/apply_infra.py                      # plan, check, apply, re-plan
    python scripts/apply_infra.py --allow-delete 'cloudflare_dns_record.test'

Every setting that differs between repositories is in `infra-apply.toml` at the repository
root: the infra folder, the committed variable files, the state backend, the 1Password
references for secrets and personal values, and the optional steps after an apply. This file
is the same in every repository that uses it, so a fix made here is copied, not rewritten.

What one run does:

1. Reads every secret the config names in ONE `op inject` call, so there is one 1Password
   prompt per run. A variable already set in the environment is used as it is and not read.
   Secrets go only into the environment of the commands that need them. They are never
   printed and never written to disk.
2. `tofu init` against the configured backend, then `tofu plan` with the committed variable
   files, into a plan file in a temporary folder that is deleted afterwards.
3. Checks the planned actions. Creates and in-place updates pass. A delete, a replace, a
   forget or any action this script does not know stops the run, unless the caller names
   that resource with --allow-delete, --allow-replace or --allow-forget. Each allowance
   must match a planned change, so an allowance for a plan that did not happen also stops
   the run. Nothing is applied when the check fails.
4. Applies the saved plan (exactly what was checked), runs any --run steps, then plans again
   with -detailed-exitcode and reports whether the infrastructure now matches the code.
5. Optionally starts the deploy workflow (--deploy) and waits for the domain mappings and
   their certificates, then smoke-tests the live names (--check-live MINUTES).

Exit codes: 0 done and clean, 1 usage or setup error, 2 a command failed, 3 the plan was
refused, 4 applied but the re-plan still shows changes, 5 the live check failed.

Standard library only, Python 3.11 or later. Works the same on Windows, macOS and Linux.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

ENGINE_VERSION = "1"
REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "infra-apply.toml"

# Actions that never need an allowance. "no-op" and "read" are not changes at all.
QUIET = (["no-op"], ["read"])
SAFE = {"create", "update"}
ALLOWABLE = {"delete", "replace", "forget"}
# The variable files OpenTofu reads on its own (and so a local one silently changes a plan).
AUTO_LOADED = {"terraform.tfvars", "terraform.tfvars.json"}
AUTO_LOADED_SUFFIXES = (".auto.tfvars", ".auto.tfvars.json")


class Refused(Exception):
    """The plan holds a change the caller did not allow."""


# ---------------------------------------------------------------------------- plan policy


@dataclass(frozen=True)
class Change:
    address: str
    kind: str  # create, update, delete, replace, forget, or the raw actions for anything else


def kind_of(actions: list[str]) -> str | None:
    """Name a resource change's actions, or None when it changes nothing."""
    if actions in QUIET:
        return None
    if actions in (["create"], ["update"], ["delete"], ["forget"]):
        return actions[0]
    if sorted(actions) == ["create", "delete"]:
        return "replace"  # delete-then-create, or create-before-destroy
    return "+".join(actions) or "empty"


def changes(plan: dict) -> list[Change]:
    """Every change in `tofu show -json` output, in plan order."""
    out = []
    for rc in plan.get("resource_changes", []):
        k = kind_of(list(rc.get("change", {}).get("actions", [])))
        if k is not None:
            out.append(Change(rc["address"], k))
    return out


def covers(allowed: str, address: str) -> bool:
    """True if an allowance names this address.

    An exact address names one instance. An address without an index, like
    `cloudflare_dns_record.test`, names every instance of that resource (`...test["a"]`,
    `...test[0]`), the way `tofu plan -target` does. Nothing else matches: no prefixes.
    """
    if allowed == address:
        return True
    return "[" not in allowed and address.startswith(allowed + "[")


def check(found: list[Change], allow: dict[str, list[str]]) -> list[str]:
    """Return the reasons to refuse this plan; an empty list means it may be applied."""
    problems = []
    used: set[tuple[str, str]] = set()
    for c in found:
        if c.kind in SAFE:
            continue
        if c.kind not in ALLOWABLE:
            problems.append(f"{c.address}: unknown action '{c.kind}', never applied by this script")
            continue
        hits = [a for a in allow.get(c.kind, []) if covers(a, c.address)]
        if not hits:
            problems.append(f"{c.address}: {c.kind} not allowed (pass --allow-{c.kind} {c.address})")
        used.update((c.kind, a) for a in hits)
    for kind, addrs in allow.items():
        for a in addrs:
            if (kind, a) not in used:
                problems.append(f"--allow-{kind} {a}: matches no {kind} in this plan")
    return problems


def summary(found: list[Change]) -> str:
    sign = {"create": "+", "update": "~", "delete": "-", "replace": "-/+", "forget": "forget"}
    if not found:
        return "  no changes"
    return "\n".join(f"  {sign.get(c.kind, '?'):>6} {c.address}" for c in found)


# ---------------------------------------------------------------------------- config


def load_config(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"{path} not found. It holds this repository's apply settings.")
    cfg = tomllib.loads(path.read_text(encoding="utf-8"))
    for key in ("infra_dir", "backend"):
        if key not in cfg:
            raise SystemExit(f"{path.name}: missing '{key}'")
    for key in ("bucket", "prefix"):
        if not cfg["backend"].get(key):
            raise SystemExit(f"{path.name}: missing backend.{key}")
    # [[secret]] entries, each {env, ref}, become one {env: ref} map. A list of tables rather
    # than `NAME = "op://..."` lines, which secret scanners read as a credential assignment.
    secrets = {}
    for entry in cfg.pop("secret", []):
        name, ref = entry.get("env", ""), str(entry.get("ref", ""))
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or not ref.startswith("op://"):
            raise SystemExit(f"{path.name}: each [[secret]] needs env = a variable name, ref = an op:// reference")
        if name in secrets:
            raise SystemExit(f"{path.name}: [[secret]] {name} is listed twice")
        secrets[name] = ref
    cfg["secrets"] = secrets
    return cfg


def auto_loaded(infra: Path, var_files: list[str]) -> list[str]:
    """Variable files OpenTofu loads without being told, other than the committed ones."""
    return sorted(p.name for p in infra.iterdir() if p.is_file() and p.name not in var_files
                  and (p.name in AUTO_LOADED or p.name.endswith(AUTO_LOADED_SUFFIXES)))


def outputs_changed(plan: dict) -> bool:
    """True if any output value changes. Outputs need no allowance, but they need an apply."""
    return any(list(o.get("actions", ["no-op"])) != ["no-op"]
               for o in plan.get("output_changes", {}).values())


def tfvars_strings(text: str) -> dict[str, str]:
    """Top-level `key = "value"` lines of a .tfvars file. Enough for project and region."""
    return dict(re.findall(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*)"', text, re.M))


# ---------------------------------------------------------------------------- secrets


def missing_secrets(refs: dict[str, str], env: dict[str, str]) -> dict[str, str]:
    """The references whose variable the environment does not already hold."""
    return {name: ref for name, ref in refs.items() if not env.get(name)}


def read_secrets(refs: dict[str, str]) -> dict[str, str]:
    """Resolve every reference in one `op inject` call: one 1Password prompt."""
    if not refs:
        return {}
    op = shutil.which("op")
    if not op:
        raise SystemExit("1Password CLI (op) is not on PATH; install it or set these variables: "
                         + ", ".join(refs))
    template = json.dumps({name: "{{ " + ref + " }}" for name, ref in refs.items()})
    p = subprocess.run([op, "inject"], input=template, capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        raise SystemExit(f"op inject failed: {p.stderr.strip()}")  # op's message; holds no value
    try:
        values = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise SystemExit("op inject returned something that is not JSON (not shown: it may hold a secret)")
    empty = [n for n in refs if not values.get(n)]
    if empty:
        raise SystemExit(f"1Password returned nothing for: {', '.join(empty)}")
    return {n: values[n] for n in refs}


# ---------------------------------------------------------------------------- commands


def mise() -> str:
    path = shutil.which("mise")
    if not path:
        raise SystemExit("mise is not on PATH. It pins OpenTofu (mise.toml): https://mise.jdx.dev")
    return path


def run(cmd: list[str], cwd: Path, env: dict[str, str], capture: bool = False,
        ok: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd[3:] if cmd[1:3] == ['exec', '--'] else cmd)}", flush=True)
    p = subprocess.run(cmd, cwd=cwd, env=env, text=True, encoding="utf-8",
                       capture_output=capture)
    if p.returncode not in ok:
        if capture:
            print(p.stdout[-4000:], p.stderr[-4000:], sep="\n")
        raise SystemExit(2)
    return p


def domain_ready(mapping: dict) -> tuple[bool, str]:
    """Whether a `gcloud beta run domain-mappings list --format=json` entry is Ready."""
    conds = {c.get("type"): c for c in mapping.get("status", {}).get("conditions", [])}
    ready = conds.get("Ready", {})
    if ready.get("status") == "True":
        return True, "Ready"
    waiting = [f"{t}: {c.get('message') or c.get('reason') or c.get('status')}"
               for t, c in conds.items() if c.get("status") != "True"]
    return False, "; ".join(waiting) or "no status yet"


def cert_ok(host: str) -> tuple[bool, str]:
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=15) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as s:
                return True, f"certificate valid until {s.getpeercert()['notAfter']}"
    except (OSError, ssl.SSLError) as e:
        return False, f"{type(e).__name__}: {e}"


def check_live(cfg: dict, root: Path, infra: Path, env: dict[str, str], minutes: float) -> bool:
    """Wait for every domain mapping to be Ready with a valid certificate, then smoke-test."""
    live = cfg.get("live", {})
    var_text = "".join((infra / f).read_text(encoding="utf-8") for f in cfg.get("var_files", []))
    values = tfvars_strings(var_text)
    project = live.get("project") or values.get("project_id")
    region = live.get("region") or values.get("region")
    if not (project and region):
        print("live check: no project or region in [live] or the variable files")
        return False
    gcloud = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if not gcloud:
        print("live check: gcloud is not on PATH")
        return False
    deadline = time.monotonic() + minutes * 60
    while True:
        p = subprocess.run([gcloud, "beta", "run", "domain-mappings", "list", f"--project={project}",
                            f"--region={region}", "--format=json"], capture_output=True, text=True)
        if p.returncode != 0:
            print("live check: gcloud failed:", p.stderr.strip()[-1000:])
            return False
        report = []
        for m in json.loads(p.stdout or "[]"):
            host = m.get("metadata", {}).get("name", "?")
            ok, why = domain_ready(m)
            if ok:
                ok, why = cert_ok(host)
            report.append((host, ok, why))
        if report and all(ok for _, ok, _ in report):
            break
        if time.monotonic() > deadline:
            for host, ok, why in report:
                print(f"  {'ok ' if ok else 'NOT'} {host}: {why}")
            print("live check: timed out" if report else "live check: no domain mappings found")
            return False
        print(f"  waiting: {', '.join(h for h, ok, _ in report if not ok)}", flush=True)
        time.sleep(30)
    for host, _, why in report:
        print(f"  ok  {host}: {why}")
    passed = True
    for host, script in live.get("smoke", {}).items():
        p = subprocess.run([sys.executable, str(root / script), f"https://{host}"], cwd=root, env=env)
        print(f"  smoke {host}: {'pass' if p.returncode == 0 else 'FAIL'}")
        passed = passed and p.returncode == 0
    return passed


# ---------------------------------------------------------------------------- main


def parse_args(argv: list[str], steps: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--plan-only", action="store_true", help="plan and check, apply nothing")
    for kind in sorted(ALLOWABLE):
        ap.add_argument(f"--allow-{kind}", action="append", default=[], metavar="ADDRESS",
                        help=f"allow a {kind} of this address (no index: every instance)")
    ap.add_argument("--run", action="append", default=[], choices=steps or None, metavar="STEP",
                    help="after the apply, run this step from infra-apply.toml [steps]: "
                         + (", ".join(steps) or "none configured"))
    ap.add_argument("--deploy", action="store_true", help="after the apply, start the deploy workflow")
    ap.add_argument("--check-live", type=float, metavar="MINUTES",
                    help="after the apply, wait up to MINUTES for mappings and certificates, then smoke")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # Read --config first: the step names that --run accepts come from it.
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path, default=CONFIG)
    cfg_path = pre.parse_known_args(argv)[0].config.resolve()
    cfg = load_config(cfg_path)
    root = cfg_path.parent  # every path in the config is relative to the config file
    steps = cfg.get("steps", {})
    args = parse_args(argv, sorted(steps))
    allow = {k: getattr(args, f"allow_{k}") for k in ALLOWABLE if getattr(args, f"allow_{k}")}

    infra = (root / cfg["infra_dir"]).resolve()
    var_files = cfg.get("var_files", [])
    for f in var_files:
        if not (infra / f).is_file():
            raise SystemExit(f"{infra / f} not found (named in {cfg_path.name})")
    stray = auto_loaded(infra, var_files)
    if stray:
        print(f"WARNING: {', '.join(stray)} in {infra.name}/ is not committed, and OpenTofu loads it "
              f"automatically. The committed files {var_files} win where both set a key, but it "
              "overrides the TF_VAR_* values read from 1Password. Move anything else into the "
              "committed files or into a [[secret]], then delete it.", flush=True)

    secret_env = {**os.environ}
    secret_env.update(read_secrets(missing_secrets(cfg.get("secrets", {}), os.environ)))
    plain_env = {k: v for k, v in secret_env.items() if k not in cfg.get("secrets", {})}

    m = mise()
    tofu = [m, "exec", "--", "tofu"]
    var_args = [f"-var-file={f}" for f in var_files]
    be = cfg["backend"]
    run([*tofu, "init", "-input=false", f"-backend-config=bucket={be['bucket']}",
         f"-backend-config=prefix={be['prefix']}"], infra, secret_env, capture=True)

    with tempfile.TemporaryDirectory(prefix="apply-infra-") as tmp:
        plan_file = str(Path(tmp) / "infra.tfplan")
        run([*tofu, "plan", "-input=false", *var_args, f"-out={plan_file}"], infra, secret_env)
        shown = run([*tofu, "show", "-json", plan_file], infra, secret_env, capture=True)
        planned = json.loads(shown.stdout)
        found = changes(planned)
        outputs = outputs_changed(planned)
        print(f"\nPlanned changes ({len(found)}):\n{summary(found)}\n"
              + ("  and output values change\n" if outputs else ""))
        problems = check(found, allow)
        if problems:
            print("REFUSED, nothing applied:\n  " + "\n  ".join(problems))
            return 3
        if args.plan_only:
            print("Plan only: the check passed, nothing applied.")
            return 0
        if found or outputs:
            run([*tofu, "apply", "-input=false", plan_file], infra, secret_env)
        else:
            print("Nothing to apply.")

    for name in args.run:
        run([m, "exec", "--", *steps[name]], root, plain_env)

    again = run([*tofu, "plan", "-input=false", "-detailed-exitcode", *var_args], infra, secret_env,
                capture=True, ok=(0, 2))
    clean = again.returncode == 0
    print("Re-plan: clean, the infrastructure matches the code." if clean else
          "Re-plan: NOT clean, it still shows changes:\n" + again.stdout[-3000:])

    if args.deploy:
        wf = cfg.get("deploy_workflow")
        if not wf:
            raise SystemExit(f"--deploy: no deploy_workflow in {cfg_path.name}")
        gh = shutil.which("gh")
        if not gh:
            raise SystemExit("--deploy: gh is not on PATH")
        run([gh, "workflow", "run", wf, "--ref", cfg.get("deploy_ref", "main")], root, plain_env)
    if args.check_live is not None and not check_live(cfg, root, infra, plain_env, args.check_live):
        return 5
    return 0 if clean else 4


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
