# The private Docker repository the deploy workflow pushes images to and Cloud Run pulls
# them from. One regional repository, beside the service.

resource "google_artifact_registry_repository" "site" {
  repository_id = "site"
  location      = var.region
  format        = "DOCKER"
  description   = "Images of the static site, pushed by GitHub Actions via Workload Identity Federation."

  # Each image is a few megabytes, but they accumulate with every merge. Keep the recent
  # ones (enough to roll back to) and delete the rest, which keeps storage inside the
  # free allowance.
  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 20
    }
  }
  # The deploy workflow moves the live tag to each image it promotes. Keeping it means the
  # serving image survives any number of pushes that were never promoted.
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
      older_than = "2592000s" # 30 days; anything still among the 20 newest is kept above
    }
  }
}

# Cloud Run pulls images as its service agent, not as the runtime service account. Grant
# that agent read on this repository explicitly rather than relying on a project default.
resource "google_artifact_registry_repository_iam_member" "cloud_run_pull" {
  repository = google_artifact_registry_repository.site.name
  location   = google_artifact_registry_repository.site.location
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:service-${data.google_project.this.number}@serverless-robot-prod.iam.gserviceaccount.com"

  # That agent is created when the Cloud Run API is first used. Creating the service first
  # guarantees it exists, so the grant cannot fail on a fresh project. (If it ever did,
  # running `tofu apply` again would complete it.)
  depends_on = [google_cloud_run_v2_service.site]
}
