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

      # A revision is Ready, and so eligible for traffic, only once / answers 200.
      startup_probe {
        http_get {
          path = "/"
          port = 8080
        }
        initial_delay_seconds = 0
        timeout_seconds       = 2
        period_seconds        = 3
        failure_threshold     = 10
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      # gcloud's deploy stamps its own client labels and annotations on the revision
      # template (and names the revision). Without these, the next `tofu apply` after a
      # deploy would roll a new revision only to remove them.
      template[0].labels,
      template[0].annotations,
      template[0].revision,
      # gcloud's image-only deploy writes service-level scaling and client fields that this
      # config does not declare; ignoring them keeps `tofu plan` clean after a deploy.
      scaling,
      client,
      client_version,
      traffic,
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
