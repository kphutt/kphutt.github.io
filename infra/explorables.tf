# explorables.kphutt.com: standalone interactive pages (explorables/), on their own origin.
#
# The main site's policy is script-src 'none'. An explorable runs its own script files, so it
# is served from a second Cloud Run service on a subdomain, a separate origin as far as the
# browser is concerned. The site keeps its policy and its gate unchanged, and nothing on this
# origin can reach the site's pages. AGENTS.md, "Explorables", has the rules.
#
# This file holds the service, its public access and its domain mapping. The rest sits where
# its kind already lives:
#
#   dns.tf          the CNAME to ghs.googlehosted.com, among the service records
#   identities.tf   the deployer's run.developer on this service
#   cost-cutoff.tf  the cutoff turns this service off too
#   uptime.tf       the uptime and certificate-expiry checks for this name
#
# It runs as the site's empty runtime account and pulls from the site's image repository, so
# it needs no new identity and no new repository grant.

# The same shape as the site's service (cloud-run.tf), and like it, template[0].revision is
# NOT ignored.
#
# Why: when a revision is created with a name, that name is written into the service's
# template. gcloud writes one whenever it deploys with --tag or --revision-suffix (gcloud's
# own code, surface/run/services/update.py: generate_name). If OpenTofu then ignored the
# field, a later apply of any template change would send that existing name back with a
# different configuration, and Cloud Run refuses that with 409 "Revision named ... with
# different configuration already exists".
#
# So two things keep this service clear of it:
#
#   1. its deploy job (.github/workflows/hugo.yml, explorables-deploy) never passes --tag or
#      --revision-suffix when it creates a revision. Cloud Run names the revision itself and
#      the template keeps no name. The job tags the revision afterwards, with a traffic-only
#      update, and fails before any traffic moves if the template has a name anyway.
#      scripts/check_infra.py refuses either flag in every deploy workflow;
#   2. the field is not ignored here. It is optional and not computed in the provider, so if
#      a name ever does land in the template (a deploy by hand with --tag, say), the plan
#      shows it being removed, and the apply sends no name. Cloud Run then names the new
#      revision itself. No 409, at the cost of one visible line in that one plan.
resource "google_cloud_run_v2_service" "explorables" {
  name     = "explorables"
  location = var.region

  ingress = "INGRESS_TRAFFIC_ALL"

  deletion_protection = false

  template {
    service_account = google_service_account.runtime.email

    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instances
    }

    max_instance_request_concurrency = 80

    containers {
      image = "us-docker.pkg.dev/cloudrun/container/hello"

      ports {
        container_port = 8080 # server/explorables.sws.toml listens here
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "256Mi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      # Set explicitly, as for the site: leaving the block out keeps the last probe the API
      # reported.
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
      # gcloud's client labels and annotations, and the nonce label it adds to force a new
      # revision when it does not name one. Resending them names nothing.
      template[0].labels,
      template[0].annotations,
      scaling,
      client,
      client_version,
      traffic,
      # The cost cutoff (cost-cutoff.tf) turns this service off too, by ingress. A later
      # apply must not reopen it.
      ingress,
    ]
  }
}

resource "google_cloud_run_v2_service_iam_member" "explorables_public" {
  name     = google_cloud_run_v2_service.explorables.name
  location = google_cloud_run_v2_service.explorables.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

locals {
  # What the mapping asks for: explorables is a subdomain of the verified kphutt.com
  # (site-domain.tf), so one CNAME, as for www. dns.tf holds the same record, and
  # tests/dns.tftest.hcl checks that the two match.
  explorables_mapping_records = {
    "cname" = { type = "CNAME", content = "ghs.googlehosted.com" }
  }

  explorables_mapping_expected = toset([for r in values(local.explorables_mapping_records) : "${r.type} ${lower(r.content)}"])
}

# DNS points at the mapping, so deleting it takes the name off the internet: prevent_destroy,
# as for the apex and www.
resource "google_cloud_run_domain_mapping" "explorables" {
  location = var.region
  name     = "explorables.${local.zone_name}"

  metadata {
    namespace = var.project_id
  }

  spec {
    route_name = google_cloud_run_v2_service.explorables.name
  }

  depends_on = [google_site_verification_web_resource.apex]

  lifecycle {
    prevent_destroy = true

    postcondition {
      condition = try(
        length(self.status[0].resource_records) == length(local.explorables_mapping_records)
        && toset([for r in self.status[0].resource_records : "${r.type} ${lower(trimsuffix(r.rrdata, "."))}"]) == local.explorables_mapping_expected,
        false
      )
      error_message = "The explorables.kphutt.com mapping asked for DNS records other than local.explorables_mapping_records (explorables.tf). Read them with `gcloud beta run domain-mappings describe --domain explorables.kphutt.com` and change that list, and dns.tf's, in review."
    }
  }
}
