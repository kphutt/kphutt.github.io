# Two service accounts, never shared.
#
#   runtime   what the Cloud Run service runs as. It needs no permissions: the server
#             only reads files baked into its own image. Running as a dedicated empty
#             identity, rather than the project's default compute account, means a bug in
#             the server can reach nothing.
#
#   deployer  what GitHub Actions becomes, via Workload Identity Federation (no key ever
#             exists). It can push images to the one repository and deploy revisions of
#             the site's service and the explorables service (explorables.tf). It cannot
#             change IAM, infra or anything else: infra changes are applied by hand with
#             `tofu apply` (infra/README.md).

resource "google_service_account" "runtime" {
  account_id   = "site-runtime"
  display_name = "Site: Cloud Run runtime (no permissions)"
}

resource "google_service_account" "deployer" {
  account_id   = "site-deployer"
  display_name = "Site: GitHub Actions deployer (WIF only, no keys)"
}

# Push images to this repository only.
resource "google_artifact_registry_repository_iam_member" "deployer_push" {
  repository = google_artifact_registry_repository.site.name
  location   = google_artifact_registry_repository.site.location
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.deployer.email}"
}

# Deploy revisions of, and move traffic on, this service only. run.developer cannot change
# the service's IAM policy, and the only identity it can run a revision as is the empty
# runtime account, so a stolen deployer credential cannot reach anything else in Google
# Cloud. It can still serve any content on the site, change the service's settings, or
# delete the service: that is what deploying is, which is why only main may do it.
resource "google_cloud_run_v2_service_iam_member" "deployer_deploy" {
  name     = google_cloud_run_v2_service.site.name
  location = google_cloud_run_v2_service.site.location
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.deployer.email}"
}

# The same, on the explorables service and nothing more. Its images go to the same
# repository (deployer_push above) and it runs as the same runtime account (below), so this
# one binding is all the deployer gains.
resource "google_cloud_run_v2_service_iam_member" "deployer_deploys_explorables" {
  name     = google_cloud_run_v2_service.explorables.name
  location = google_cloud_run_v2_service.explorables.location
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.deployer.email}"
}

# Deploying a revision that runs as the runtime account requires acting as it.
resource "google_service_account_iam_member" "deployer_acts_as_runtime" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

# ── Workload Identity Federation ─────────────────────────────────────────────────────────
# A GitHub Actions OIDC token is exchanged for short-lived credentials of the deployer,
# but only when every claim below matches. Two gates, both required:
#   1. the provider's attribute_condition: anything else is refused before any credential
#      exists;
#   2. the binding's principalSet: only identities from this repository may become the
#      deployer.

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions"
  description               = "Keyless deploys from the site repository's main branch."
}

locals {
  # The only workflow files that may deploy, as they exist on main. A new workflow, or
  # one on another branch, gets nothing even though it is in the same repository.
  deploy_workflows = [
    "${var.github_repository}/.github/workflows/hugo.yml@refs/heads/main",
    "${var.github_repository}/.github/workflows/rollback.yml@refs/heads/main",
  ]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.repository_id" = "assertion.repository_id"
    "attribute.ref"           = "assertion.ref"
    "attribute.workflow_ref"  = "assertion.workflow_ref"
    "attribute.environment"   = "assertion.environment"
  }

  # Every term must hold:
  #   - this repository, by numeric id (a name can be re-registered after a rename);
  #   - the main branch (a pull request runs on refs/pull/..., so it never qualifies);
  #   - only the deploy and rollback workflow files;
  #   - only a job in the cloud-run environment, so a job in those files that does not name
  #     that environment cannot use it, even with an OIDC token;
  #   - only a push, a manual run or a schedule. All three build main exactly as it is.
  #     Triggers such as pull_request_target or workflow_run also run on main while
  #     handling another branch's input, so adding one to either file must not quietly
  #     gain deploy rights.
  # All of this assumes only reviewed code reaches main (infra/README.md).
  attribute_condition = join(" && ", [
    "assertion.repository_id == \"${var.github_repository_id}\"",
    "assertion.ref == \"refs/heads/main\"",
    "assertion.workflow_ref in ${jsonencode(local.deploy_workflows)}",
    "assertion.environment == \"cloud-run\"",
    "assertion.event_name in [\"push\", \"workflow_dispatch\", \"schedule\"]",
  ])

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_becomes_deployer" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/${var.github_repository_id}"
}
