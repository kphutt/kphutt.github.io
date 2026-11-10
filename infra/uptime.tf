# Uptime checks and the two alerts that matter once the site is on Cloud Run: it is down, or
# its TLS certificate is about to expire. Both email the existing alert channel
# (monitoring.tf). Alerts, not dashboards: nothing here is meant to be looked at.
#
# Cloud Run's Google-managed certificate renews by itself roughly every 90 days. A renewal
# that fails stays silent until the old certificate expires and browsers refuse the site, so
# the certificate alert fires with plenty of time left to fix it.
#
# To watch the apex and www after the move, add them to uptime_hostnames (terraform.tfvars):
#
#   uptime_hostnames = ["next.kphutt.com", "kphutt.com", "www.kphutt.com"]
#
# Cost: $0. Uptime checks get 1,000,000 free executions a month per billing account, and each
# region counts as one execution per period. 3 names x 3 regions x one check per 5 minutes
# is about 78,000 a month. Alert policies on uptime metrics are not charged, even after
# Google starts charging for alerting (not before 2027-09-01). Source:
# https://cloud.google.com/stackdriver/pricing (uptime-check execution, alerting).

variable "uptime_hostnames" {
  description = "Hostnames to watch over HTTPS (GET /, expect 200, valid certificate). Add kphutt.com and www.kphutt.com once the site has moved."
  type        = list(string)
  default     = ["next.kphutt.com"]

  validation {
    condition = (
      length(var.uptime_hostnames) > 0
      && alltrue([for h in var.uptime_hostnames : can(regex("^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?[.])*kphutt[.]com$", h))])
    )
    error_message = "uptime_hostnames must be a non-empty list of lowercase kphutt.com names (the apex or a subdomain)."
  }
}

variable "uptime_cert_expiry_days" {
  description = "Email when a certificate has fewer than this many days left. Google renews its managed certificates well before this, so reaching it means a renewal has failed."
  type        = number
  default     = 14

  validation {
    condition     = var.uptime_cert_expiry_days >= 1 && var.uptime_cert_expiry_days <= 30
    error_message = "uptime_cert_expiry_days must be between 1 and 30."
  }
}

locals {
  # Three regions: enough for "down from two or more" to mean the site, not one checker's
  # network. Keep it at 3 or more or the down alert can never fire.
  uptime_regions = ["USA_OREGON", "USA_VIRGINIA", "EUROPE"]

  uptime_check_filter = join(" OR ", [for c in google_monitoring_uptime_check_config.site : "metric.label.check_id = \"${c.uptime_check_id}\""])
}

resource "google_monitoring_uptime_check_config" "site" {
  for_each     = toset(var.uptime_hostnames)
  display_name = "Site ${each.value}"
  period       = "300s"
  timeout      = "10s"

  http_check {
    path           = "/"
    port           = 443
    use_ssl        = true
    validate_ssl   = true
    request_method = "GET"

    accepted_response_status_codes {
      status_value = 200
    }
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = each.value
    }
  }

  selected_regions = local.uptime_regions
}

resource "google_monitoring_alert_policy" "site_down" {
  display_name = "Site down"
  combiner     = "OR"

  conditions {
    display_name = "Check failing from 2 or more regions for 10 minutes"
    condition_threshold {
      filter = "metric.type = \"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type = \"uptime_url\" AND (${local.uptime_check_filter})"

      # Count, per host, how many regions report a failed check. More than 1 means 2 or more.
      aggregations {
        alignment_period     = "1200s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "600s"

      trigger {
        count = 1
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]

  documentation {
    mime_type = "text/markdown"
    subject   = "Site down: $${resource.label.host}"
    content   = <<-EOT
      What it means: the uptime check for this site has failed from two or more regions for about 10 minutes. Visitors probably cannot load it, or the certificate no longer validates.

      What to do:
      1. Open the site in a browser and check the status of the Cloud Run service `${var.service_name}` in project `${var.project_id}`.
      2. Look at the service's recent logs and revisions. If a deploy caused it, roll back (infra/README.md).
      3. If the site is fine for you, check the certificate and the DNS records for the host named above.
      4. If the cost cutoff has turned the site off (cost-cutoff.md), this alert is expected. Reopen the site per that doc.
    EOT
  }
}

resource "google_monitoring_alert_policy" "cert_expiring" {
  display_name = "TLS certificate expiring"
  combiner     = "OR"

  conditions {
    display_name = "Certificate has fewer than ${var.uptime_cert_expiry_days} days left"
    condition_threshold {
      filter = "metric.type = \"monitoring.googleapis.com/uptime_check/time_until_ssl_cert_expires\" AND resource.type = \"uptime_url\" AND (${local.uptime_check_filter})"

      # The smallest value any region sees, per host. The metric is in days.
      aggregations {
        alignment_period     = "1200s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_MIN"
        group_by_fields      = ["resource.label.host"]
      }
      comparison      = "COMPARISON_LT"
      threshold_value = var.uptime_cert_expiry_days
      duration        = "600s"

      trigger {
        count = 1
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]

  documentation {
    mime_type = "text/markdown"
    subject   = "TLS certificate expiring: $${resource.label.host}"
    content   = <<-EOT
      What it means: the certificate for the host named above has fewer than ${var.uptime_cert_expiry_days} days left. Google renews its managed certificates long before that, so a renewal has failed. When it expires, browsers will refuse the site.

      What to do:
      1. Check the domain mapping for the host in project `${var.project_id}` (region ${var.region}): its certificate status says why provisioning is stuck.
      2. Confirm DNS for the host still points where the domain mapping says (a changed or removed record stops renewal).
      3. If it does not recover, delete and recreate the domain mapping in OpenTofu to force a new certificate, and do it well before the expiry date.
    EOT
  }
}
