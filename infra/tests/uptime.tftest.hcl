# Offline tests for the uptime checks and alerts (uptime.tf): `tofu init -backend=false && tofu test`.
#
# Providers are mocked, as in plan.tftest.hcl. These pin what a silent mistake would defeat:
# what is checked, what counts as failing, when each alert fires, and who is emailed.

mock_provider "google" {
  mock_data "google_project" {
    defaults = {
      number = "123456789012"
    }
  }
  mock_resource "google_service_account" {
    defaults = {
      name  = "projects/test-site-project/serviceAccounts/site-mocked@test-site-project.iam.gserviceaccount.com"
      email = "site-mocked@test-site-project.iam.gserviceaccount.com"
      id    = "projects/test-site-project/serviceAccounts/site-mocked@test-site-project.iam.gserviceaccount.com"
    }
  }
  mock_resource "google_iam_workload_identity_pool" {
    defaults = {
      name = "projects/123456789012/locations/global/workloadIdentityPools/github-actions"
    }
  }
  mock_resource "google_project_iam_custom_role" {
    defaults = {
      name = "projects/test-site-project/roles/siteCostCutoff"
    }
  }
  mock_resource "google_monitoring_notification_channel" {
    defaults = {
      id = "projects/test-site-project/notificationChannels/1"
    }
  }
  mock_resource "google_monitoring_uptime_check_config" {
    defaults = {
      uptime_check_id = "next-kphutt-com-abc123"
    }
  }
  mock_resource "google_pubsub_topic" {
    defaults = {
      id = "projects/test-site-project/topics/site-cost-cutoff"
    }
  }
}

mock_provider "google-beta" {}
mock_provider "cloudflare" {}

variables {
  project_id           = "test-site-project"
  region               = "us-west1"
  github_repository    = "example/site"
  github_repository_id = "42"
  billing_account      = "000000-000000-000000"
  alert_email          = "alerts@example.com"
  cloudflare_zone_id   = "0123456789abcdef0123456789abcdef"
  # `tofu test` cannot run import blocks (dns.tf).
  import_production_dns = false
}

run "checks_the_test_name_by_default" {
  command = plan

  assert {
    condition     = keys(google_monitoring_uptime_check_config.site) == ["next.kphutt.com"]
    error_message = "By default only next.kphutt.com is checked."
  }
  assert {
    condition = (
      google_monitoring_uptime_check_config.site["next.kphutt.com"].http_check[0].path == "/"
      && google_monitoring_uptime_check_config.site["next.kphutt.com"].http_check[0].port == 443
      && google_monitoring_uptime_check_config.site["next.kphutt.com"].http_check[0].use_ssl == true
    )
    error_message = "The check must be an HTTPS GET of / on 443."
  }
  assert {
    condition     = google_monitoring_uptime_check_config.site["next.kphutt.com"].http_check[0].validate_ssl == true
    error_message = "The check must validate the certificate, or a bad one goes unseen."
  }
  assert {
    condition     = google_monitoring_uptime_check_config.site["next.kphutt.com"].http_check[0].accepted_response_status_codes[0].status_value == 200
    error_message = "The check must expect exactly 200."
  }
  assert {
    condition     = google_monitoring_uptime_check_config.site["next.kphutt.com"].period == "300s"
    error_message = "The check runs every 5 minutes."
  }
  assert {
    condition     = length(google_monitoring_uptime_check_config.site["next.kphutt.com"].selected_regions) >= 3
    error_message = "Three or more regions are needed, or the down alert (2 regions failing) can never fire for one name."
  }
}

run "down_alert_needs_two_regions_for_ten_minutes" {
  command = plan

  assert {
    condition     = google_monitoring_alert_policy.site_down.conditions[0].condition_threshold[0].comparison == "COMPARISON_GT" && google_monitoring_alert_policy.site_down.conditions[0].condition_threshold[0].threshold_value == 1
    error_message = "Down means more than one region reporting a failed check."
  }
  assert {
    condition     = google_monitoring_alert_policy.site_down.conditions[0].condition_threshold[0].aggregations[0].cross_series_reducer == "REDUCE_COUNT_FALSE"
    error_message = "The down alert must count failing regions."
  }
  assert {
    condition     = google_monitoring_alert_policy.site_down.conditions[0].condition_threshold[0].duration == "600s"
    error_message = "The down alert waits 10 minutes."
  }
  assert {
    condition     = strcontains(google_monitoring_alert_policy.site_down.conditions[0].condition_threshold[0].filter, "uptime_check/check_passed") && strcontains(google_monitoring_alert_policy.site_down.conditions[0].condition_threshold[0].filter, "next-kphutt-com-abc123")
    error_message = "The down alert must read check_passed for the checks this file creates."
  }
  assert {
    condition     = google_monitoring_alert_policy.site_down.notification_channels == tolist([google_monitoring_notification_channel.email.id])
    error_message = "The down alert must email the existing alert channel."
  }
  assert {
    condition     = strcontains(google_monitoring_alert_policy.site_down.documentation[0].content, "What to do")
    error_message = "The down alert must say what to do."
  }
}

run "cert_alert_fires_below_the_threshold" {
  command = plan

  assert {
    condition     = google_monitoring_alert_policy.cert_expiring.conditions[0].condition_threshold[0].comparison == "COMPARISON_LT" && google_monitoring_alert_policy.cert_expiring.conditions[0].condition_threshold[0].threshold_value == 14
    error_message = "The certificate alert fires below 14 days."
  }
  assert {
    condition     = strcontains(google_monitoring_alert_policy.cert_expiring.conditions[0].condition_threshold[0].filter, "uptime_check/time_until_ssl_cert_expires")
    error_message = "The certificate alert must read the SSL expiry metric."
  }
  assert {
    condition     = google_monitoring_alert_policy.cert_expiring.conditions[0].condition_threshold[0].aggregations[0].cross_series_reducer == "REDUCE_MIN"
    error_message = "The certificate alert must use the smallest days-left any region sees."
  }
  assert {
    condition     = google_monitoring_alert_policy.cert_expiring.notification_channels == tolist([google_monitoring_notification_channel.email.id])
    error_message = "The certificate alert must email the existing alert channel."
  }
  assert {
    condition     = strcontains(google_monitoring_alert_policy.cert_expiring.documentation[0].content, "What to do")
    error_message = "The certificate alert must say what to do."
  }
}

run "apex_and_www_are_one_line_to_add" {
  command = plan

  variables {
    uptime_hostnames        = ["next.kphutt.com", "kphutt.com", "www.kphutt.com"]
    uptime_cert_expiry_days = 21
  }

  assert {
    condition     = length(google_monitoring_uptime_check_config.site) == 3
    error_message = "Each listed name gets its own check."
  }
  assert {
    condition     = google_monitoring_alert_policy.cert_expiring.conditions[0].condition_threshold[0].threshold_value == 21
    error_message = "uptime_cert_expiry_days must set the certificate threshold."
  }
}

run "rejects_foreign_hostnames" {
  command = plan

  variables {
    uptime_hostnames = ["example.com"]
  }

  expect_failures = [var.uptime_hostnames]
}
