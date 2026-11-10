# The cost cutoff: turn the site off automatically when the month's actual spend reaches
# cost_cutoff_usd. How it works, why it turns the site off this way, and how to reopen it:
# cost-cutoff.md.
#
#   budget "Site cost cutoff" ──publishes several times a day──▶ Pub/Sub topic
#        │                                                          │
#        └─ emails the alert channel when spend crosses the cutoff  ▼
#                                          function site-cost-cutoff (cost-cutoff/main.py)
#                                                                   │ costAmount >= cutoff
#                                                                   ▼
#                                          site's ingress := internal only
#
# Everything here stays inside free allowances: the budget, the topic and the function's few
# thousand calls a month cost nothing, the source bucket holds one small zip in a free-tier
# region, and the image repository keeps only the newest build.

variable "cost_cutoff_usd" {
  description = "Actual spend this month, in whole US dollars, at which the site is turned off. Forecasts never trip it."
  type        = number
  default     = 10

  validation {
    condition     = var.cost_cutoff_usd >= 1 && floor(var.cost_cutoff_usd) == var.cost_cutoff_usd
    error_message = "cost_cutoff_usd must be a whole number of dollars, at least 1."
  }
}

locals {
  # The function turns this service off, and nothing else.
  cutoff_target = "projects/${var.project_id}/locations/${var.region}/services/${google_cloud_run_v2_service.site.name}"
}

# ── The budget and its topic ─────────────────────────────────────────────────────────────
# A second budget beside the $1 alert in monitoring.tf, so the alert's emails are unchanged.
# Its amount is the cutoff. Cloud Billing sends every update to the topic; the function
# decides from costAmount alone. The one threshold emails the existing channel when actual
# spend crosses the cutoff, which is the moment the function turns the site off.

resource "google_pubsub_topic" "cost_cutoff" {
  name = "site-cost-cutoff"
}

resource "google_billing_budget" "cutoff" {
  provider        = google-beta
  billing_account = var.billing_account
  display_name    = "Site cost cutoff (turns the site off)"

  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.cost_cutoff_usd)
    }
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "CURRENT_SPEND"
  }

  # Creating this grants Cloud Billing the Pub/Sub Publisher role on the topic (it needs
  # the operator's pubsub.topics.setIamPolicy, which a project owner has). The topic's IAM
  # policy is not managed here, so OpenTofu never removes that grant.
  all_updates_rule {
    pubsub_topic                     = google_pubsub_topic.cost_cutoff.id
    schema_version                   = "1.0"
    monitoring_notification_channels = [google_monitoring_notification_channel.email.id]
    disable_default_iam_recipients   = false
  }
}

# ── Identities ───────────────────────────────────────────────────────────────────────────
#   cutoff  what the function runs as, and what Eventarc uses to call it. It can read and
#           update the one site service, through a custom role of three permissions.
#   build   what Cloud Build runs as when it packages the function. It can write logs,
#           push to the function's own image repository and read the function's source.

resource "google_service_account" "cost_cutoff" {
  account_id   = "site-cost-cutoff"
  display_name = "Site: cost cutoff function (can only change the site's settings)"
}

resource "google_service_account" "cost_cutoff_build" {
  account_id   = "site-cost-cutoff-build"
  display_name = "Site: builds the cost cutoff function"
}

# Read the service, update it, and follow the update. Not delete, not IAM, not anything in
# another service. (run.services.update could in principle deploy another image; the only
# code holding it is cost-cutoff/main.py, which sends nothing but the ingress field.)
resource "google_project_iam_custom_role" "cost_cutoff" {
  role_id     = "siteCostCutoff"
  title       = "Site cost cutoff"
  description = "Read and update one Cloud Run service. Bound on the site service only."
  permissions = ["run.services.get", "run.services.update", "run.operations.get"]
}

resource "google_cloud_run_v2_service_iam_member" "cost_cutoff_updates_site" {
  name     = google_cloud_run_v2_service.site.name
  location = google_cloud_run_v2_service.site.location
  role     = google_project_iam_custom_role.cost_cutoff.name
  member   = "serviceAccount:${google_service_account.cost_cutoff.email}"
}

# Cloud Run asks for actAs on a service's identity when that service is updated. The site
# runs as the runtime account, which holds no permissions at all (identities.tf), so acting
# as it reaches nothing.
resource "google_service_account_iam_member" "cost_cutoff_acts_as_runtime" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.cost_cutoff.email}"
}

# Eventarc calls the function as the cutoff account.
resource "google_cloud_run_v2_service_iam_member" "cost_cutoff_invokes_itself" {
  name     = google_cloudfunctions2_function.cost_cutoff.name
  location = google_cloudfunctions2_function.cost_cutoff.location
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.cost_cutoff.email}"
}

resource "google_project_iam_member" "cost_cutoff_build_logs" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.cost_cutoff_build.email}"
}

resource "google_artifact_registry_repository_iam_member" "cost_cutoff_build_push" {
  repository = google_artifact_registry_repository.cost_cutoff.name
  location   = google_artifact_registry_repository.cost_cutoff.location
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.cost_cutoff_build.email}"
}

# Cloud Functions copies the source into its own gcf-v2-sources-* bucket and builds from
# there, so the build reads that bucket as well as ours. The condition keeps it out of
# every other bucket in the project, the OpenTofu state bucket among them.
resource "google_project_iam_member" "cost_cutoff_build_source" {
  project = var.project_id
  role    = "roles/storage.objectViewer"
  member  = "serviceAccount:${google_service_account.cost_cutoff_build.email}"

  condition {
    title       = "cost-cutoff-source-only"
    description = "Only the function's source buckets."
    expression = join(" || ", [
      "resource.name.startsWith(\"projects/_/buckets/gcf-v2-sources-\")",
      "resource.name == \"projects/_/buckets/${google_storage_bucket.cost_cutoff_source.name}\"",
      "resource.name.startsWith(\"projects/_/buckets/${google_storage_bucket.cost_cutoff_source.name}/\")",
    ])
  }
}

# ── Source and images ────────────────────────────────────────────────────────────────────
# Only main.py and requirements.txt go in the zip; the tests stay out. The object name
# carries the zip's hash, so a code change replaces the object and redeploys the function.

data "archive_file" "cost_cutoff" {
  type        = "zip"
  output_path = "${path.module}/.terraform/cost-cutoff.zip"

  source {
    filename = "main.py"
    content  = file("${path.module}/cost-cutoff/main.py")
  }
  source {
    filename = "requirements.txt"
    content  = file("${path.module}/cost-cutoff/requirements.txt")
  }
}

resource "google_storage_bucket" "cost_cutoff_source" {
  name                        = "${var.project_id}-cost-cutoff-source"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
}

resource "google_storage_bucket_object" "cost_cutoff_source" {
  name   = "cost-cutoff-${data.archive_file.cost_cutoff.output_md5}.zip"
  bucket = google_storage_bucket.cost_cutoff_source.name
  source = data.archive_file.cost_cutoff.output_path
}

# The function's build images, kept apart from the site's so neither cleanup policy has to
# know about the other. Only the newest image is needed: the function is rebuilt from source.
resource "google_artifact_registry_repository" "cost_cutoff" {
  repository_id = "cost-cutoff"
  location      = var.region
  format        = "DOCKER"
  description   = "Build images of the cost cutoff function."

  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "keep-newest"
    action = "KEEP"
    most_recent_versions {
      keep_count = 1
    }
  }
  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "86400s" # a day; the newest is kept above
    }
  }
}

# ── The function ─────────────────────────────────────────────────────────────────────────

resource "google_cloudfunctions2_function" "cost_cutoff" {
  name        = "site-cost-cutoff"
  location    = var.region
  description = "Turns the site off when the month's actual spend reaches the cutoff."

  build_config {
    runtime           = "python313"
    entry_point       = "handle"
    service_account   = google_service_account.cost_cutoff_build.id
    docker_repository = google_artifact_registry_repository.cost_cutoff.id
    source {
      storage_source {
        bucket = google_storage_bucket.cost_cutoff_source.name
        object = google_storage_bucket_object.cost_cutoff_source.name
      }
    }
  }

  service_config {
    min_instance_count = 0
    max_instance_count = 1
    available_memory   = "256M"
    timeout_seconds    = 60
    # Only Eventarc, in this project, can reach it; the internet cannot.
    ingress_settings               = "ALLOW_INTERNAL_ONLY"
    all_traffic_on_latest_revision = true
    service_account_email          = google_service_account.cost_cutoff.email
    environment_variables = {
      SERVICE    = local.cutoff_target
      CUTOFF_USD = tostring(var.cost_cutoff_usd)
    }
  }

  event_trigger {
    trigger_region        = var.region
    event_type            = "google.cloud.pubsub.topic.v1.messagePublished"
    pubsub_topic          = google_pubsub_topic.cost_cutoff.id
    service_account_email = google_service_account.cost_cutoff.email
    # A failed update (an API error) raises, and Pub/Sub delivers the message again.
    retry_policy = "RETRY_POLICY_RETRY"
  }

  # The build fails without these, so grant them first.
  depends_on = [
    google_project_iam_member.cost_cutoff_build_logs,
    google_project_iam_member.cost_cutoff_build_source,
    google_artifact_registry_repository_iam_member.cost_cutoff_build_push,
  ]
}

output "cost_cutoff_reopen_command" {
  description = "Reopens the site after the cost cutoff has turned it off. Read cost-cutoff.md first."
  value       = "gcloud run services update ${google_cloud_run_v2_service.site.name} --region=${var.region} --project=${var.project_id} --ingress=all"
}
