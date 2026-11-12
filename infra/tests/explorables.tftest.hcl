# Offline plan tests for explorables.kphutt.com (explorables.tf, and its parts in dns.tf,
# identities.tf, cost-cutoff.tf and uptime.tf): `tofu init -backend=false && tofu test`.
#
# Providers are mocked, as in plan.tftest.hcl. These pin what would be costly to get wrong:
# the second service costs nothing when idle, the deployer gains this one service and nothing
# else, the mapping asks for exactly one CNAME, the cost cutoff turns it off too, and its
# template never carries a revision name for a later apply to resend.

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

# Its own id, so the alert filters can be seen to include it.
override_resource {
  target = google_monitoring_uptime_check_config.explorables
  values = {
    uptime_check_id = "explorables-kphutt-com-def456"
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

run "explorables_service_scales_to_zero_and_is_public" {
  command = plan

  assert {
    condition     = google_cloud_run_v2_service.explorables.name == "explorables" && google_cloud_run_v2_service.explorables.location == "us-west1"
    error_message = "The service is explorables, in the site's region."
  }
  assert {
    condition     = google_cloud_run_v2_service.explorables.template[0].scaling[0].min_instance_count == 0
    error_message = "min instances above 0 costs money every hour."
  }
  assert {
    condition     = google_cloud_run_v2_service.explorables.template[0].scaling[0].max_instance_count == var.max_instances
    error_message = "The same instance cap as the site, which caps the cost of a flood."
  }
  assert {
    condition     = google_cloud_run_v2_service.explorables.template[0].containers[0].ports[0].container_port == 8080
    error_message = "The container port must match server/explorables.sws.toml."
  }
  assert {
    condition = (
      google_cloud_run_v2_service.explorables.template[0].containers[0].startup_probe[0].tcp_socket[0].port == 8080
      && google_cloud_run_v2_service.explorables.template[0].containers[0].startup_probe[0].period_seconds == 1
      && length(google_cloud_run_v2_service.explorables.template[0].containers[0].startup_probe[0].http_get) == 0
    )
    error_message = "The startup probe is an explicit TCP check of 8080 every second, as for the site."
  }
  assert {
    condition     = google_cloud_run_v2_service.explorables.template[0].containers[0].resources[0].cpu_idle == true
    error_message = "CPU is allocated only while a request is handled."
  }
  assert {
    condition     = google_cloud_run_v2_service.explorables.template[0].service_account == google_service_account.runtime.email
    error_message = "It runs as the site's empty runtime account."
  }
  assert {
    condition     = google_cloud_run_v2_service.explorables.template[0].revision == null
    error_message = "The template must not name a revision: a later apply would resend the name and get a 409."
  }
  assert {
    condition     = google_cloud_run_v2_service.explorables.ingress == "INGRESS_TRAFFIC_ALL"
    error_message = "It starts open to all traffic (the cost cutoff closes it later; explorables.tf ignores ingress changes)."
  }
  assert {
    condition     = google_cloud_run_v2_service_iam_member.explorables_public.member == "allUsers" && google_cloud_run_v2_service_iam_member.explorables_public.name == google_cloud_run_v2_service.explorables.name
    error_message = "Anyone may request the explorables service."
  }
}

run "deployer_gains_only_the_explorables_service" {
  command = plan

  assert {
    condition     = google_cloud_run_v2_service_iam_member.deployer_deploys_explorables.role == "roles/run.developer"
    error_message = "The deployer gets run.developer, which cannot change IAM."
  }
  assert {
    condition     = google_cloud_run_v2_service_iam_member.deployer_deploys_explorables.name == google_cloud_run_v2_service.explorables.name
    error_message = "The binding is on the explorables service only, never the project."
  }
  assert {
    condition     = google_cloud_run_v2_service_iam_member.deployer_deploys_explorables.member == "serviceAccount:${google_service_account.deployer.email}"
    error_message = "The binding is for the deployer."
  }
  assert {
    condition     = length(regexall("workflows/", google_iam_workload_identity_pool_provider.github.attribute_condition)) == 2
    error_message = "Explorables deploy from hugo.yml; no new workflow may federate."
  }
  assert {
    condition     = output.github_variables["EXPLORABLES_SERVICE"] == "explorables"
    error_message = "The deploy job reads the service name from EXPLORABLES_SERVICE."
  }
}

run "maps_explorables_kphutt_com_by_one_cname" {
  command = plan

  assert {
    condition     = google_cloud_run_domain_mapping.explorables.name == "explorables.kphutt.com" && google_cloud_run_domain_mapping.explorables.spec[0].route_name == google_cloud_run_v2_service.explorables.name
    error_message = "explorables.kphutt.com maps to the explorables service."
  }
  assert {
    condition     = google_cloud_run_domain_mapping.explorables.metadata[0].namespace == "test-site-project" && google_cloud_run_domain_mapping.explorables.location == "us-west1"
    error_message = "The mapping is in the site's project and region."
  }
  assert {
    condition = (
      cloudflare_dns_record.services["explorables"].name == "explorables.kphutt.com"
      && toset(["${cloudflare_dns_record.services["explorables"].type} ${cloudflare_dns_record.services["explorables"].content}"]) == local.explorables_mapping_expected
    )
    error_message = "The DNS record must be exactly what the mapping asks for (local.explorables_mapping_records)."
  }
  assert {
    condition     = cloudflare_dns_record.services["explorables"].proxied == false
    error_message = "DNS only: a proxy breaks Google's certificate renewal (AGENTS.md)."
  }
}

run "an_explorables_mapping_asking_for_addresses_is_refused" {
  command = plan

  override_resource {
    target = google_cloud_run_domain_mapping.explorables
    values = {
      status = [{
        conditions          = []
        observed_generation = 1
        mapped_route_name   = "explorables"
        resource_records = [
          { name = "explorables", type = "A", rrdata = "216.239.32.21" },
        ]
      }]
    }
  }

  expect_failures = [google_cloud_run_domain_mapping.explorables]
}

run "an_explorables_mapping_asking_for_another_cname_is_refused" {
  command = plan

  override_resource {
    target = google_cloud_run_domain_mapping.explorables
    values = {
      status = [{
        conditions          = []
        observed_generation = 1
        mapped_route_name   = "explorables"
        resource_records = [
          { name = "explorables", type = "CNAME", rrdata = "ghs.example.com." },
        ]
      }]
    }
  }

  expect_failures = [google_cloud_run_domain_mapping.explorables]
}

run "an_explorables_mapping_asking_for_an_extra_record_is_refused" {
  command = plan

  override_resource {
    target = google_cloud_run_domain_mapping.explorables
    values = {
      status = [{
        conditions          = []
        observed_generation = 1
        mapped_route_name   = "explorables"
        resource_records = [
          { name = "explorables", type = "CNAME", rrdata = "ghs.googlehosted.com." },
          { name = "explorables", type = "CNAME", rrdata = "ghs.googlehosted.com." },
        ]
      }]
    }
  }

  expect_failures = [google_cloud_run_domain_mapping.explorables]
}

run "an_explorables_mapping_asking_for_nothing_is_refused" {
  command = plan

  override_resource {
    target = google_cloud_run_domain_mapping.explorables
    values = {
      status = [{
        conditions          = []
        observed_generation = 1
        mapped_route_name   = "explorables"
        resource_records = [

        ]
      }]
    }
  }

  expect_failures = [google_cloud_run_domain_mapping.explorables]
}

run "the_cost_cutoff_turns_explorables_off_too" {
  command = plan

  assert {
    condition     = google_cloudfunctions2_function.cost_cutoff.service_config[0].environment_variables["SERVICES"] == "projects/test-site-project/locations/us-west1/services/site,projects/test-site-project/locations/us-west1/services/explorables"
    error_message = "The function must turn off both public services."
  }
  assert {
    condition     = google_cloud_run_v2_service_iam_member.cost_cutoff_updates_explorables.name == google_cloud_run_v2_service.explorables.name && google_cloud_run_v2_service_iam_member.cost_cutoff_updates_explorables.role == google_project_iam_custom_role.cost_cutoff.name
    error_message = "The cutoff's custom role is bound on the explorables service, never the project."
  }
  assert {
    condition     = strcontains(output.cost_cutoff_reopen_command, "services update explorables ") && strcontains(output.cost_cutoff_reopen_command, "services update site ")
    error_message = "The reopen command reopens both services."
  }
}

run "the_uptime_alerts_watch_explorables" {
  command = plan

  assert {
    condition = (
      google_monitoring_uptime_check_config.explorables.monitored_resource[0].labels["host"] == "explorables.kphutt.com"
      && google_monitoring_uptime_check_config.explorables.http_check[0].path == "/"
      && google_monitoring_uptime_check_config.explorables.http_check[0].validate_ssl == true
      && google_monitoring_uptime_check_config.explorables.http_check[0].accepted_response_status_codes[0].status_value == 200
    )
    error_message = "explorables.kphutt.com is checked over HTTPS at its landing page, with a valid certificate, ending at 200."
  }
  assert {
    condition     = length(google_monitoring_uptime_check_config.explorables.selected_regions) >= 3
    error_message = "Three or more regions, or the down alert can never fire."
  }
  assert {
    condition = (
      strcontains(google_monitoring_alert_policy.site_down.conditions[0].condition_threshold[0].filter, "explorables-kphutt-com-def456")
      && strcontains(google_monitoring_alert_policy.cert_expiring.conditions[0].condition_threshold[0].filter, "explorables-kphutt-com-def456")
    )
    error_message = "Both alerts must cover the explorables check."
  }
  assert {
    condition     = strcontains(google_monitoring_alert_policy.site_down.conditions[0].condition_threshold[0].filter, "next-kphutt-com-abc123")
    error_message = "The site's own checks stay in the filter."
  }
}
