#!/usr/bin/env python3
"""Point the `live` tag in Artifact Registry at the image that is now serving.

    python scripts/move_live_tag.py REGION-docker.pkg.dev/PROJECT/REPO/IMAGE sha256:DIGEST

The registry's cleanup policy (infra/artifact-registry.tf) keeps whatever is tagged
`live`, so the serving image can never be deleted out from under a cold start. The deploy
job runs this after promoting a revision, and the rollback job after rolling back.

It moves the tag with `gcloud artifacts tags update`, which needs tags.update, and creates
it the first time with `tags create`. Both are in the deployer's roles/artifactregistry.writer.
`gcloud artifacts docker tags add` would not do: on an existing tag it deletes and
recreates it, and the writer role has no tags.delete, so every deploy after the first
would fail after traffic had already moved.
"""

from __future__ import annotations

import re
import subprocess
import sys

TAG = "live"


def main() -> int:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    image, digest = sys.argv[1], sys.argv[2]
    m = re.fullmatch(r"([a-z0-9-]+)-docker\.pkg\.dev/([a-z][a-z0-9-]+)/([a-z0-9-]+)/([a-z0-9._/-]+)", image)
    if not m:
        sys.exit(f"not an Artifact Registry image path: {image!r}")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        sys.exit(f"not an image digest: {digest!r}")
    location, project, repository, package = m.groups()
    common = [f"--location={location}", f"--project={project}", f"--repository={repository}",
              f"--package={package}", f"--version={digest}", "--quiet"]

    update = subprocess.run(["gcloud", "artifacts", "tags", "update", TAG, *common],
                            capture_output=True, text=True)
    if update.returncode == 0:
        print(f"moved {TAG} to {digest}")
        return 0
    if "NOT_FOUND" not in update.stderr and "not found" not in update.stderr.lower():
        print(update.stderr, file=sys.stderr)
        sys.exit(f"could not move the {TAG} tag")

    create = subprocess.run(["gcloud", "artifacts", "tags", "create", TAG, *common])
    if create.returncode != 0:
        sys.exit(f"could not create the {TAG} tag")
    print(f"created {TAG} at {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
