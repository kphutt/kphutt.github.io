#!/usr/bin/env python3
"""The one-time steps OpenTofu cannot do for itself, and the hand-off to GitHub.

    python infra/seed.py project   # create the project, link billing, enable APIs, state bucket
    python infra/seed.py vars      # after `tofu apply`: set the deploy workflow's repo variables

Each prints the exact commands it would run and runs nothing. Add --apply to run them.
Reads project_id, region and billing_account from infra/terraform.tfvars, so there is one
place those values are written down. Every command is safe to re-run: a step whose
result already exists is reported and skipped.

Why these are not in OpenTofu: the project and its billing link are what OpenTofu runs
inside, the state bucket is where OpenTofu keeps its memory, and the APIs must be on
before OpenTofu can call them. The repository variables live in GitHub, not GCP.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

INFRA = Path(__file__).resolve().parent
TFVARS = INFRA / "terraform.tfvars"

APIS = [
    "serviceusage.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",  # WIF: minting the deployer's short-lived token
    "sts.googleapis.com",  # WIF: exchanging the GitHub token
    "billingbudgets.googleapis.com",
    "monitoring.googleapis.com",
    "logging.googleapis.com",
]


def tfvars() -> dict:
    if not TFVARS.is_file():
        sys.exit(f"{TFVARS} not found: copy terraform.tfvars.example to it and fill it in first.")
    text = TFVARS.read_text(encoding="utf-8")
    return dict(re.findall(r'^\s*([a-z_]+)\s*=\s*"([^"]*)"', text, re.M))


def tool(name: str) -> str:
    path = shutil.which(name) or shutil.which(name + ".cmd")
    if not path:
        sys.exit(f"{name} is not on PATH. Run through mise, which pins it: mise exec -- python infra/seed.py ...")
    return path


def run(cmd: list[str], apply: bool, check_cmd: list[str] | None = None) -> None:
    """Print a command; with --apply, run it unless check_cmd shows it is already done."""
    shown = " ".join(cmd)
    if not apply:
        print(f"  would run: {shown}")
        return
    if check_cmd and subprocess.run(check_cmd, capture_output=True).returncode == 0:
        print(f"  already done, skipped: {shown}")
        return
    print(f"  running: {shown}")
    subprocess.run(cmd, check=True)


def require_bucket_in_project(gcloud: str, bucket: str, pid: str) -> None:
    """Stop if a bucket with this (global) name exists but belongs to another project.

    "Already exists" must mean "already ours". Listing the project's own buckets answers
    that without trusting any field of a bucket that may not be ours to read.
    """
    if subprocess.run([gcloud, "storage", "buckets", "describe", bucket], capture_output=True).returncode != 0:
        return  # absent (or unreadable, in which case creating it fails loudly below)
    ours = subprocess.run([gcloud, "storage", "buckets", "list", f"--project={pid}", "--format=value(name)"],
                          capture_output=True, text=True, check=True).stdout.split()
    if bucket.removeprefix("gs://") not in ours:
        sys.exit(f"{bucket} exists but is not in project {pid}. Pick another name; it must never hold this state.")


def project(apply: bool) -> None:
    v = tfvars()
    gcloud = tool("gcloud")
    pid, region, billing = v["project_id"], v["region"], v["billing_account"]
    bucket = f"gs://{pid}-tofu-state"

    print(f"Project {pid}:")
    run([gcloud, "projects", "create", pid, "--name=Personal site"], apply,
        [gcloud, "projects", "describe", pid])
    run([gcloud, "billing", "projects", "link", pid, f"--billing-account={billing}"], apply)
    run([gcloud, "services", "enable", *APIS, f"--project={pid}"], apply)

    print(f"State bucket {bucket}:")
    if apply:
        require_bucket_in_project(gcloud, bucket, pid)
    run([gcloud, "storage", "buckets", "create", bucket, f"--project={pid}", f"--location={region}",
         "--uniform-bucket-level-access", "--public-access-prevention"], apply,
        [gcloud, "storage", "buckets", "describe", bucket])
    # Versioning keeps every earlier state, so a bad apply can be walked back.
    run([gcloud, "storage", "buckets", "update", bucket, "--versioning"], apply)

    print("\nNext, from infra/ (review the plan before applying):")
    print(f'  tofu init -backend-config="bucket={pid}-tofu-state" -backend-config="prefix=site"')
    print("  tofu plan -out=site.tfplan")
    print("  tofu apply site.tfplan")
    print("  python seed.py vars --apply")


def variables(apply: bool) -> None:
    tofu = tool("tofu")
    gh = tool("gh")
    out = subprocess.run([tofu, "output", "-json", "github_variables"], cwd=INFRA,
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"tofu output failed (has `tofu apply` run?):\n{out.stderr}")
    v = tfvars()
    repo = v["github_repository"]
    print(f"Repository variables on {repo}:")
    for name, value in json.loads(out.stdout).items():
        run([gh, "variable", "set", name, "--repo", repo, "--body", value], apply)


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--apply"]
    apply = "--apply" in sys.argv[1:]
    steps = {"project": project, "vars": variables}
    if len(args) != 1 or args[0] not in steps:
        sys.exit(__doc__)
    if not apply:
        print("Dry run. Nothing is created; add --apply to run these.\n")
    steps[args[0]](apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
