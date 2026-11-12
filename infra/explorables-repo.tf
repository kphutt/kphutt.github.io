# The explorables repository (var.explorables_github_repository) deploys the explorables
# service from its own workflows, with its own identity. explorables.tf has the service; this
# file has only what that repository may do to it.
#
# The narrowest grant that lets it deploy, and nothing of the site's:
#
#   explorables-deployer   a service account of its own, never the site's deployer. It gets
#                          run.developer on the explorables service only, writer on its own
#                          image repository only, and serviceAccountUser on the runtime account
#                          (below). Nothing on the site's service, the site's image repository,
#                          IAM, DNS or the project.
#   explorables registry   its own Artifact Registry repository, so a push or a `live` tag move
#                          from that repository can never touch the site's images. Same cleanup
#                          rules as the site's (artifact-registry.tf).
#   github-explorables     its own Workload Identity Federation provider in the same pool, whose
#                          condition admits only that repository, by numeric id, on main, from
#                          its two deploy workflows, in the cloud-run environment.
#
# The service keeps running as the site's runtime account, which holds no permissions at all
# (identities.tf). Deploying a revision that runs as it requires acting as it, so the new
# deployer gets serviceAccountUser there. That lets it run code as an identity that can reach
# nothing, on the one service it can deploy. A runtime account of its own would also need the
# cost cutoff (cost-cutoff.tf) to act as it, for no gain in what either can reach.
#
# Until explorables_github_repository_id is set (the repository has to exist first), the
# provider and the binding that lets that repository become the deployer are not created, and
# nothing can use the deployer.
#
# The site repository's own explorables deploy (deployer_deploys_explorables in
# identities.tf) stays until that repository's explorables jobs are removed.

locals {
  explorables_repo_enabled = var.explorables_github_repository_id != ""

  # The only workflow files in that repository that may deploy, as they exist on main.
  explorables_deploy_workflows = [
    "${var.explorables_github_repository}/.github/workflows/ci.yml@refs/heads/main",
    "${var.explorables_github_repository}/.github/workflows/rollback.yml@refs/heads/main",
  ]
}

resource "google_service_account" "explorables_deployer" {
  account_id   = "explorables-deployer"
  display_name = "Explorables: GitHub Actions deployer (WIF only, no keys)"
}

resource "google_artifact_registry_repository" "explorables" {
  repository_id = "explorables"
  location      = var.region
  format        = "DOCKER"
  description   = "Images of explorables.kphutt.com, pushed by the explorables repository via Workload Identity Federation."

  # The same rules as the site's repository (artifact-registry.tf): keep the 20 newest and
  # whatever is tagged live, delete the rest after 30 days.
  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 20
    }
  }
  cleanup_policies {
    id     = "keep-live"
    action = "KEEP"
    condition {
      tag_state    = "TAGGED"
      tag_prefixes = ["live"]
    }
  }
  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "2592000s"
    }
  }
}

# Cloud Run pulls as its service agent, as for the site's repository.
resource "google_artifact_registry_repository_iam_member" "cloud_run_pulls_explorables" {
  repository = google_artifact_registry_repository.explorables.name
  location   = google_artifact_registry_repository.explorables.location
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:service-${data.google_project.this.number}@serverless-robot-prod.iam.gserviceaccount.com"

  depends_on = [google_cloud_run_v2_service.site]
}

# Push images to the explorables repository only.
resource "google_artifact_registry_repository_iam_member" "explorables_deployer_push" {
  repository = google_artifact_registry_repository.explorables.name
  location   = google_artifact_registry_repository.explorables.location
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.explorables_deployer.email}"
}

# Deploy revisions of, and move traffic on, the explorables service only.
resource "google_cloud_run_v2_service_iam_member" "explorables_deployer_deploy" {
  name     = google_cloud_run_v2_service.explorables.name
  location = google_cloud_run_v2_service.explorables.location
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.explorables_deployer.email}"
}

# The service runs as the empty runtime account; deploying it means acting as that account.
resource "google_service_account_iam_member" "explorables_deployer_acts_as_runtime" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.explorables_deployer.email}"
}

# Its own provider, in the same pool as the site's. Every term must hold, as for the site's
# (identities.tf): that repository by numeric id, main, only its two deploy workflow files,
# only a job in the cloud-run environment, and only a push or a manual run (it has no
# scheduled deploy).
resource "google_iam_workload_identity_pool_provider" "github_explorables" {
  count = local.explorables_repo_enabled ? 1 : 0

  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-explorables"
  display_name                       = "GitHub OIDC (explorables)"

  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.repository_id" = "assertion.repository_id"
    "attribute.ref"           = "assertion.ref"
    "attribute.workflow_ref"  = "assertion.workflow_ref"
    "attribute.environment"   = "assertion.environment"
  }

  attribute_condition = join(" && ", [
    "assertion.repository_id == \"${var.explorables_github_repository_id}\"",
    "assertion.ref == \"refs/heads/main\"",
    "assertion.workflow_ref in ${jsonencode(local.explorables_deploy_workflows)}",
    "assertion.environment == \"cloud-run\"",
    "assertion.event_name in [\"push\", \"workflow_dispatch\"]",
  ])

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# Only identities from that repository id may become the explorables deployer. The pool's
# principal sets span both providers, so this stays safe because neither provider admits the
# other repository's id: the site's admits only var.github_repository_id, this one only
# var.explorables_github_repository_id.
resource "google_service_account_iam_member" "explorables_github_becomes_deployer" {
  count = local.explorables_repo_enabled ? 1 : 0

  service_account_id = google_service_account.explorables_deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/${var.explorables_github_repository_id}"
}
