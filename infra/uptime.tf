# Uptime checks and the two alerts that matter: the site is down, or its TLS certificate is
# about to expire. Both email the existing alert channel (monitoring.tf). Alerts, not
# dashboards: nothing here is meant to be looked at.
#
# Cloud Run serves kphutt.com and www, and Google renews their certificates automatically.
# Nothing in this setup emails when a renewal fails, so the certificate alert warns once fewer
# than 14 days remain (the default).
#
# Google follows every redirect and judges the final answer, so a name that redirects (www
# goes to the apex) is held to the final 200. That still proves its certificate handshake and
# that the redirect lands on a live page. For such a name the certificate-expiry alert
# measures the final host's certificate. Every name must end at 200. The checks fetch
# /robots.txt, a few hundred bytes, so they add almost no outbound data.
#
# explorables.kphutt.com (explorables.tf) is watched too, by its own check of its landing
# page: it has no robots.txt, on purpose. Both alerts cover it.
#
# Cost: $0. Uptime checks get 1,000,000 free executions a month per billing account, and each
# region counts as one execution per period. 3 names x 3 regions x one check per 15 minutes
# is about 26,000 a month. Alert policies on uptime metrics are not charged, even after
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
  description = "Email when a certificate has fewer than this many days left. This is the alert threshold."
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

  uptime_check_filter = join(" OR ", [
    for c in concat(values(google_monitoring_uptime_check_config.site), [google_monitoring_uptime_check_config.explorables]) :
    "metric.label.check_id = \"${c.uptime_check_id}\""
  ])
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

# The landing page, a few hundred bytes. It is the only page that always exists.
resource "google_monitoring_uptime_check_config" "explorables" {
  display_name = "Site explorables.kphutt.com"
  period       = "900s"
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
      host       = google_cloud_run_domain_mapping.explorables.name
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
      1. Open the site in a browser. Then check that public DNS still points at Google:
         - kphutt.com A: 216.239.32.21, 216.239.34.21, 216.239.36.21, 216.239.38.21
         - kphutt.com AAAA: 2001:4860:4802:32::15, 2001:4860:4802:34::15, 2001:4860:4802:36::15, 2001:4860:4802:38::15
         - www.kphutt.com CNAME: ghs.googlehosted.com
         - explorables.kphutt.com CNAME: ghs.googlehosted.com

         If they differ, compare with infra/dns.tf (infra/README.md, "Production DNS").
      2. Check the Cloudflare records are DNS-only (not proxied). See AGENTS.md, "Do not turn the DNS proxy on".
      3. Check the domain mapping status (`gcloud beta run domain-mappings describe --domain <name> --region ${var.region} --project ${var.project_id}`) and the service `${var.service_name}` (for explorables.kphutt.com, the service `${google_cloud_run_v2_service.explorables.name}`). The status can lag behind what is served, so trust a real HTTPS request over it. For a bad deploy, use the `rollback.yml` workflow.
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
      What it means: the certificate served for the host named in this alert has fewer than ${var.uptime_cert_expiry_days} days left. For www, that is kphutt.com's certificate, because the check follows www's redirect to kphutt.com. This alert does not cover www's own certificate. If it expires before Google replaces it, browsers will refuse the site.

      What to do:
      1. Check that public DNS still points at Google, with the records in infra/dns.tf. kphutt.com should have four A and four AAAA records, and www and explorables should each be a CNAME to ghs.googlehosted.com.
      2. Also check that the records are DNS-only (not proxied). Google renews only while DNS points at it and is not proxied. See AGENTS.md, "Do not turn the DNS proxy on".
      3. Check the mapping for kphutt.com (`gcloud beta run domain-mappings describe --domain kphutt.com --region ${var.region} --project ${var.project_id}`), and the same for www.kphutt.com and explorables.kphutt.com. While the certificate is pending, its CertificateProvisioned condition gives Google's reason.
      4. If DNS and the proxy are right but the certificate is still not replaced with a week left, recreate the mapping. First, in a reviewed pull request, lift its `prevent_destroy` and the matching rule in scripts/check_infra.py. The site is unreachable at that name from the deletion until Google issues the new certificate.
    EOT
  }
}
