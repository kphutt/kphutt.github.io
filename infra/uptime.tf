# Uptime checks and the two alerts that matter: the site is down, or its TLS certificate is
# about to expire. Both email the existing alert channel (monitoring.tf). Alerts, not
# dashboards: nothing here is meant to be looked at.
#
# Whoever serves the names is watched: GitHub Pages today, then Cloud Run after the cutover.
# Both renew their managed certificates by themselves. A renewal that fails stays silent until
# the old certificate expires and browsers refuse the site, so the certificate alert fires
# with plenty of time left to fix it.
#
# www answers with a 301 to the apex, so the names in uptime_redirect_hostnames accept a 301;
# every other name must answer 200. The checks fetch /robots.txt, a few hundred bytes, so they
# add almost no outbound data.
#
# Cost: $0. Uptime checks get 1,000,000 free executions a month per billing account, and each
# region counts as one execution per period. 2 names x 3 regions x one check per 15 minutes
# is about 17,000 a month. Alert policies on uptime metrics are not charged, even after
# Google starts charging for alerting (not before 2027-09-01). Source:
# https://cloud.google.com/stackdriver/pricing (uptime-check execution, alerting).

variable "uptime_hostnames" {
  description = "Hostnames to watch over HTTPS (GET /robots.txt, valid certificate). Each must answer 200, unless it is also in uptime_redirect_hostnames."
  type        = list(string)
  default     = ["kphutt.com", "www.kphutt.com"]

  validation {
    condition = (
      length(var.uptime_hostnames) > 0
      && alltrue([for h in var.uptime_hostnames : can(regex("^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?[.])*kphutt[.]com$", h))])
    )
    error_message = "uptime_hostnames must be a non-empty list of lowercase kphutt.com names (the apex or a subdomain)."
  }
}

variable "uptime_redirect_hostnames" {
  description = "Names in uptime_hostnames that redirect (301) instead of answering 200. Every other name must answer 200."
  type        = list(string)
  default     = ["www.kphutt.com"]

  validation {
    condition     = alltrue([for h in var.uptime_redirect_hostnames : contains(var.uptime_hostnames, h)])
    error_message = "uptime_redirect_hostnames must be a subset of uptime_hostnames."
  }
}

variable "uptime_cert_expiry_days" {
  description = "Email when a certificate has fewer than this many days left. GitHub Pages and Google renew their managed certificates well before this, so reaching it means a renewal has failed."
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
  period       = "900s"
  timeout      = "10s"

  http_check {
    path           = "/robots.txt"
    port           = 443
    use_ssl        = true
    validate_ssl   = true
    request_method = "GET"

    # One accepted code per name: 301 for the redirecting names, 200 for the rest.
    dynamic "accepted_response_status_codes" {
      for_each = [contains(var.uptime_redirect_hostnames, each.value) ? 301 : 200]
      content {
        status_value = accepted_response_status_codes.value
      }
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
    display_name = "Check failing from 2 or more regions for 30 minutes"
    condition_threshold {
      filter = "metric.type = \"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type = \"uptime_url\" AND (${local.uptime_check_filter})"

      # Count, per host, how many regions report a failed check. More than 1 means 2 or more.
      aggregations {
        alignment_period     = "1800s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "1800s"

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
      What it means: the uptime check for this site has failed from two or more regions for about 30 minutes. Visitors probably cannot load it, or the certificate no longer validates.

      What to do:
      1. Open the site in a browser. Check which provider answers: DNS pointing at 185.199.108-111.153 or kphutt.github.io means GitHub Pages; DNS pointing at Google means Cloud Run.
      2. If GitHub Pages serves the name: check the Cloudflare records are DNS-only (not proxied) and the Pages custom-domain and HTTPS status. See AGENTS.md, "Do not turn the DNS proxy on".
      3. If Cloud Run serves the name: check the domain mapping status (`gcloud beta run domain-mappings describe --domain <name> --region ${var.region} --project ${var.project_id}`) and the service `${var.service_name}`. For a bad deploy, use the `rollback.yml` workflow. For a DNS problem, see docs/design/cutover-runbook.md.
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
        alignment_period     = "1800s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_MIN"
        group_by_fields      = ["resource.label.host"]
      }
      comparison      = "COMPARISON_LT"
      threshold_value = var.uptime_cert_expiry_days
      duration        = "1800s"

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
      What it means: the certificate for the host named above has fewer than ${var.uptime_cert_expiry_days} days left. GitHub Pages and Google renew their managed certificates long before that, so a renewal has failed. When it expires, browsers will refuse the site.

      What to do:
      1. If GitHub Pages serves the name (DNS points at 185.199.108-111.153 or kphutt.github.io): check the Cloudflare records are DNS-only (not proxied) and the Pages custom-domain and HTTPS status. See AGENTS.md, "Do not turn the DNS proxy on".
      2. If Cloud Run serves the name (DNS points at Google): check the domain mapping status (`gcloud beta run domain-mappings describe --domain <name> --region ${var.region} --project ${var.project_id}`); its certificate status says why provisioning is stuck. Confirm DNS still points where the mapping says. For a DNS problem, see docs/design/cutover-runbook.md.
      3. If it does not recover, re-save the custom domain in Pages, or delete and recreate the domain mapping in OpenTofu, and do it well before the expiry date.
    EOT
  }
}
