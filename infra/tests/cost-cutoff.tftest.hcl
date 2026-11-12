# Offline tests for the cost cutoff (cost-cutoff.tf): `tofu init -backend=false && tofu test`.
#
# Providers are mocked, as in plan.tftest.hcl; the archive provider runs for real, which
# proves the zip is built from the function's files. These pin what would be costly to get
# wrong: when it trips, what it can touch, and who can reach it.

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
  mock_resource "google_pubsub_topic" {
    defaults = {
      id = "projects/test-site-project/topics/site-cost-cutoff"
    }
  }
}

mock_provider "google-beta" {}
mock_provider "cloudflare" {}

# kphutt.com's ownership token and mappings (site-domain.tf), in the shape Google returns:
# the token is "<label> <target>", the apex mapping asks for Google's eight addresses and
# www for one CNAME. tests/site-domain.tftest.hcl tests them; here they only have to plan.
override_data {
  target = data.google_site_verification_token.apex
  values = {
    token = "abc123def gv-mockedapex.dv.googlehosted.com"
  }
}

override_resource {
  target = google_cloud_run_domain_mapping.apex
  values = {
    status = [{
      conditions          = []
      observed_generation = 1
      mapped_route_name   = "site"
      resource_records = [
        { name = "", type = "A", rrdata = "216.239.32.21" },
        { name = "", type = "A", rrdata = "216.239.34.21" },
        { name = "", type = "A", rrdata = "216.239.36.21" },
        { name = "", type = "A", rrdata = "216.239.38.21" },
        { name = "", type = "AAAA", rrdata = "2001:4860:4802:32::15" },
        { name = "", type = "AAAA", rrdata = "2001:4860:4802:34::15" },
        { name = "", type = "AAAA", rrdata = "2001:4860:4802:36::15" },
        { name = "", type = "AAAA", rrdata = "2001:4860:4802:38::15" },
      ]
    }]
  }
}

override_resource {
  target = google_cloud_run_domain_mapping.www
  values = {
    status = [{
      conditions          = []
      observed_generation = 1
      mapped_route_name   = "site"
      resource_records = [
        { name = "www", type = "CNAME", rrdata = "ghs.googlehosted.com." },
      ]
    }]
  }
}

# explorables.kphutt.com's mapping (explorables.tf) asks for one CNAME, as www does.
# tests/explorables.tftest.hcl tests it; here it only has to plan.
override_resource {
  target = google_cloud_run_domain_mapping.explorables
  values = {
    status = [{
      conditions          = []
      observed_generation = 1
      mapped_route_name   = "explorables"
      resource_records = [
        { name = "explorables", type = "CNAME", rrdata = "ghs.googlehosted.com." },
      ]
    }]
  }
}

variables {
  apex_verification_expected = "abc123def gv-mockedapex.dv.googlehosted.com"
  project_id                 = "test-site-project"
  region                     = "us-west1"
  github_repository          = "example/site"
  github_repository_id       = "42"
  billing_account            = "000000-000000-000000"
  alert_email                = "alerts@example.com"
  cloudflare_zone_id         = "0123456789abcdef0123456789abcdef"
  # `tofu test` cannot run import blocks (dns.tf).
  import_production_dns = false
}

run "trips_on_actual_spend_at_the_cutoff" {
  command = plan

  assert {
    condition     = google_billing_budget.cutoff.amount[0].specified_amount[0].units == "10"
    error_message = "The cutoff budget's amount must be cost_cutoff_usd (default 10)."
  }
  assert {
    condition     = google_cloudfunctions2_function.cost_cutoff.service_config[0].environment_variables["CUTOFF_USD"] == "10"
    error_message = "The function must enforce the same cutoff as the budget."
  }
  assert {
    condition     = alltrue([for r in google_billing_budget.cutoff.threshold_rules : r.spend_basis == "CURRENT_SPEND"])
    error_message = "The cutoff budget must have no forecast threshold: forecasts never trip it."
  }
  assert {
    condition     = google_billing_budget.cutoff.budget_filter[0].credit_types_treatment == "EXCLUDE_ALL_CREDITS"
    error_message = "The cutoff budget must count usage before credits, or credits could hide a flood."
  }
  assert {
    condition     = google_billing_budget.cutoff.all_updates_rule[0].pubsub_topic == google_pubsub_topic.cost_cutoff.id
    error_message = "The cutoff budget must publish to the cutoff topic."
  }
  assert {
    condition     = google_billing_budget.cutoff.all_updates_rule[0].monitoring_notification_channels == tolist([google_monitoring_notification_channel.email.id])
    error_message = "A trip must email the existing alert channel."
  }
  assert {
    condition     = google_billing_budget.monthly.amount[0].specified_amount[0].units == "1"
    error_message = "The $1 alert budget must stay as it was."
  }
  assert {
    condition     = google_cloudfunctions2_function.cost_cutoff.event_trigger[0].pubsub_topic == google_pubsub_topic.cost_cutoff.id
    error_message = "The function must be triggered by the cutoff topic."
  }
  assert {
    condition     = google_cloudfunctions2_function.cost_cutoff.event_trigger[0].retry_policy == "RETRY_POLICY_RETRY"
    error_message = "A failed turn-off must be retried."
  }
}

run "targets_only_the_public_services" {
  command = plan

  assert {
    condition     = google_cloudfunctions2_function.cost_cutoff.service_config[0].environment_variables["SERVICES"] == "projects/test-site-project/locations/us-west1/services/site,projects/test-site-project/locations/us-west1/services/explorables"
    error_message = "The function must target the site and explorables services, and nothing else."
  }
  assert {
    condition     = google_cloudfunctions2_function.cost_cutoff.build_config[0].entry_point == "handle"
    error_message = "The entry point must be main.handle."
  }
}

run "least_privilege" {
  command = plan

  assert {
    condition     = toset(google_project_iam_custom_role.cost_cutoff.permissions) == toset(["run.services.get", "run.services.update", "run.operations.get"])
    error_message = "The cutoff role holds exactly these three permissions."
  }
  assert {
    condition     = google_cloud_run_v2_service_iam_member.cost_cutoff_updates_site.name == google_cloud_run_v2_service.site.name
    error_message = "The cutoff role is bound on the site service only, never the project."
  }
  assert {
    condition     = google_cloud_run_v2_service_iam_member.cost_cutoff_updates_site.role == google_project_iam_custom_role.cost_cutoff.name
    error_message = "The function gets the custom role on the site, not a predefined one."
  }
  assert {
    condition     = google_service_account_iam_member.cost_cutoff_acts_as_runtime.service_account_id == google_service_account.runtime.name
    error_message = "The function may act only as the empty runtime account."
  }
  assert {
    condition     = google_cloudfunctions2_function.cost_cutoff.service_config[0].ingress_settings == "ALLOW_INTERNAL_ONLY"
    error_message = "The internet must not be able to call the function."
  }
  assert {
    condition     = google_cloud_run_v2_service_iam_member.cost_cutoff_invokes_itself.member == "serviceAccount:${google_service_account.cost_cutoff.email}"
    error_message = "Only the cutoff identity may invoke the function (no allUsers)."
  }
  assert {
    condition     = strcontains(google_project_iam_member.cost_cutoff_build_source.condition[0].expression, "gcf-v2-sources-")
    error_message = "The build reads source buckets only, never the whole project's storage."
  }
  assert {
    condition     = google_storage_bucket.cost_cutoff_source.public_access_prevention == "enforced"
    error_message = "The source bucket must never be public."
  }
}

run "stays_free" {
  command = plan

  assert {
    condition     = google_cloudfunctions2_function.cost_cutoff.service_config[0].min_instance_count == 0
    error_message = "The function must scale to zero."
  }
  assert {
    condition     = google_cloudfunctions2_function.cost_cutoff.service_config[0].max_instance_count == 1
    error_message = "One instance is plenty for a few messages a day."
  }
  assert {
    condition     = contains(["us-west1", "us-central1", "us-east1"], google_storage_bucket.cost_cutoff_source.location)
    error_message = "The source bucket must be in a free-tier region."
  }
}

run "source_zip_holds_only_the_function" {
  command = plan

  assert {
    condition     = endswith(google_storage_bucket_object.cost_cutoff_source.name, "${data.archive_file.cost_cutoff.output_md5}.zip")
    error_message = "The object name must change with the code, so a change redeploys."
  }
}

run "cutoff_can_be_raised" {
  command = plan

  variables {
    cost_cutoff_usd = 25
  }

  assert {
    condition     = google_billing_budget.cutoff.amount[0].specified_amount[0].units == "25"
    error_message = "The budget follows cost_cutoff_usd."
  }
  assert {
    condition     = google_cloudfunctions2_function.cost_cutoff.service_config[0].environment_variables["CUTOFF_USD"] == "25"
    error_message = "The function follows cost_cutoff_usd."
  }
}

run "fractional_cutoff_is_refused" {
  command = plan

  variables {
    cost_cutoff_usd = 2.5
  }

  expect_failures = [var.cost_cutoff_usd]
}

run "zero_cutoff_is_refused" {
  command = plan

  variables {
    cost_cutoff_usd = 0
  }

  expect_failures = [var.cost_cutoff_usd]
}
