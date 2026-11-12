# The Cloud Run service that serves the site.
#
# OpenTofu creates it on Google's public placeholder image. From then on the deploy
# workflow owns which image runs and how traffic is split (blue-green: candidate at 0%,
# smoke, promote), so those are ignored here and a later `tofu apply` can never roll a
# deploy back or clobber a rollback.

data "google_project" "this" {
  project_id = var.project_id
}

resource "google_cloud_run_v2_service" "site" {
  name     = var.service_name
  location = var.region

  # Public: it is a public website.
  ingress = "INGRESS_TRAFFIC_ALL"

  # The service holds no data; the image is rebuilt from the repo on every deploy.
  deletion_protection = false

  template {
    service_account = google_service_account.runtime.email

    # Scale to zero: no request, no instance, no cost.
    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instances
    }

    # One instance serves many requests at once; a file server is I/O-bound.
    max_instance_request_concurrency = 80

    containers {
      image = "us-docker.pkg.dev/cloudrun/container/hello"

      ports {
        container_port = 8080 # server/sws.toml listens here
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "256Mi"
        }
        # CPU is only allocated while a request is being handled, so idle time is free.
        cpu_idle          = true
        startup_cpu_boost = true
      }

      # Ready once the port opens. The deploy's smoke test checks every page before a revision
      # gets traffic, so a page check here only adds wait. Leaving the block out would keep the
      # last probe the API reported, so it is set explicitly.
      startup_probe {
        tcp_socket {
          port = 8080
        }
        initial_delay_seconds = 0
        timeout_seconds       = 1
        period_seconds        = 1
        failure_threshold     = 10
      }

    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      # gcloud's deploy stamps its own client labels and annotations on the revision
      # template. Without these, the next `tofu apply` after a deploy would roll a new
      # revision only to remove them.
      template[0].labels,
      template[0].annotations,
      # template[0].revision is NOT ignored. It must stay empty, so that Cloud Run names
      # each revision. Ignoring it would make every template change here resend the name
      # of the revision now serving, and Cloud Run refuses that with a 409. The deploy
      # never names a revision (hugo.yml), so a plan after a deploy has nothing to change.
      # A revision this file makes gets no traffic, because traffic below stays pinned to
      # the revision the deploy promoted by name. Only the deploy's smoke test promotes.
      # gcloud's image-only deploy writes service-level scaling and client fields that this
      # config does not declare; ignoring them keeps `tofu plan` clean after a deploy.
      scaling,
      client,
      client_version,
      traffic,
      # The cost cutoff (cost-cutoff.tf) turns the site off by setting ingress to internal
      # only. Ignoring it means a later `tofu apply` cannot quietly reopen a tripped site;
      # reopening is a deliberate command (cost-cutoff.md).
      ingress,
    ]
  }
}

# Anyone may request the site. The runtime service account holds no permissions at all,
# so a public service exposes nothing but the files in the image.
resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.site.name
  location = google_cloud_run_v2_service.site.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
