# Cost and logs.

# ── Budget alert ─────────────────────────────────────────────────────────────────────────
# The site should cost $0 a month. This is the after-the-fact backstop: it emails when
# actual spend crosses any of the thresholds, and the first one is a cent's worth of
# surprise rather than a bill.

resource "google_monitoring_notification_channel" "email" {
  display_name = "Site alerts"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
}

resource "google_billing_budget" "monthly" {
  provider        = google-beta
  billing_account = var.billing_account
  display_name    = "Site monthly budget"

  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(floor(var.monthly_budget_usd))
    }
  }

  threshold_rules {
    threshold_percent = 0.1
    spend_basis       = "CURRENT_SPEND"
  }
  threshold_rules {
    threshold_percent = 0.5
    spend_basis       = "CURRENT_SPEND"
  }
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "CURRENT_SPEND"
  }
  # Forecast too, so a traffic flood is flagged before the month's bill exists.
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  all_updates_rule {
    monitoring_notification_channels = [google_monitoring_notification_channel.email.id]
    disable_default_iam_recipients   = false
  }
}

# ── Request logs ─────────────────────────────────────────────────────────────────────────
# Cloud Run's request log (status, path, referrer, user agent, latency, client address)
# is the site's only analytics. It is routed to a dedicated bucket and excluded from the
# _Default bucket, so each request is stored exactly once. A sink added later (to
# BigQuery, say) reads from the same filter without creating a second copy in _Default.

locals {
  request_log_filter = join(" AND ", [
    "resource.type=\"cloud_run_revision\"",
    "resource.labels.service_name=\"${var.service_name}\"",
    "log_id(\"run.googleapis.com/requests\")",
  ])
}

resource "google_logging_project_bucket_config" "requests" {
  project        = var.project_id
  location       = "global"
  bucket_id      = "site-requests"
  retention_days = var.request_log_retention_days
  description    = "Cloud Run request logs for the site: the analytics record."
}

resource "google_logging_project_sink" "requests" {
  name                   = "site-requests"
  destination            = "logging.googleapis.com/${google_logging_project_bucket_config.requests.id}"
  filter                 = local.request_log_filter
  unique_writer_identity = true
}

# Keep the same entries out of _Default, so they are not stored twice.
resource "google_logging_project_exclusion" "requests_from_default" {
  name        = "site-requests-not-in-default"
  description = "Site request logs live in the site-requests bucket only."
  filter      = local.request_log_filter
}
