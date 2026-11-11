# Uptime checks and the two alerts that matter: the site is down, or its TLS certificate is
# about to expire. Both email the existing alert channel (monitoring.tf). Alerts, not
# dashboards: nothing here is meant to be looked at.
#
# Cloud Run serves both names, and Google renews their certificates by itself. A renewal that
# fails stays silent until the old certificate expires and browsers refuse the site. So the
# certificate alert fires with plenty of time left to fix it.
#
# Google follows every redirect and judges the final answer, so a name that redirects (www
# goes to the apex) is held to the final 200. That still proves its certificate handshake and
# that the redirect lands on a live page. For such a name the certificate-expiry alert
# measures the final host's certificate. Every name must end at 200. The checks fetch
# /robots.txt, a few hundred bytes, so they add almost no outbound data.
#
# Cost: $0. Uptime checks get 1,000,000 free executions a month per billing account, and each
# region counts as one execution per period. 2 names x 3 regions x one check per 15 minutes
# is about 17,000 a month. Alert policies on uptime metrics are not charged, even after
# Google starts charging for alerting (not before 2027-09-01). Source:
# https://cloud.google.com/stackdriver/pricing (uptime-check execution, alerting).

variable "uptime_hostnames" {
  description = "Hostnames to watch over HTTPS (GET /robots.txt, valid certificate). Each must end at 200 after any redirects."
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
  period       = "900s"
  timeout      = "10s"

  http_check {
    path           = "/robots.txt"
    port           = 443
    use_ssl        = true
    validate_ssl   = true
    request_method = "GET"

    # One accepted code per name: the final 200.
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
      1. Open the site in a browser. Then check public DNS still points at Google. kphutt.com should have the A records 216.239.32.21, .34.21, .36.21 and .38.21, and the AAAA records 2001:4860:4802:32::15, :34::15, :36::15 and :38::15. www should be a CNAME to ghs.googlehosted.com. If not, compare with infra/dns.tf (infra/README.md, "Production DNS").
      2. Check the Cloudflare records are DNS-only (not proxied). See AGENTS.md, "Do not turn the DNS proxy on".
      3. Check the domain mapping status (`gcloud beta run domain-mappings describe --domain <name> --region ${var.region} --project ${var.project_id}`) and the service `${var.service_name}`. The status can lag behind what is served, so trust a real HTTPS request over it. For a bad deploy, use the `rollback.yml` workflow.
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
      What it means: the certificate for the host named above has fewer than ${var.uptime_cert_expiry_days} days left. Google renews its managed certificates long before that, so a renewal has failed. When it expires, browsers will refuse the site. If the host redirects (www), this alert measures the certificate of the host it redirects to (kphutt.com), so check that one first. This alert does not cover www's own certificate.

      What to do:
      1. Check public DNS still points at Google, as in the "Site down" alert, step 1, and that the records are DNS-only (not proxied). Google renews only while both hold. See AGENTS.md, "Do not turn the DNS proxy on".
      2. Check the domain mapping status (`gcloud beta run domain-mappings describe --domain <name> --region ${var.region} --project ${var.project_id}`). Its certificate condition says why provisioning is stuck.
      3. If DNS and the proxy setting are right and it still does not recover, recreate the domain mapping in OpenTofu, well before the expiry date. The name is down from the delete until Google issues the new certificate. The mapping's `prevent_destroy` has to come off first, in a reviewed pull request.
    EOT
  }
}
