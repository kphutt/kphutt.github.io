# Offline plan tests: `tofu init -backend=false && tofu test` (from infra/).
#
# The providers are mocked, so this needs no GCP project, no credentials and no network
# beyond the provider download. It runs a real `plan` of this configuration and asserts
# the properties that would be expensive or dangerous to get wrong: who can deploy, what
# the deployer may touch, and that the service scales to zero.

mock_provider "google" {
  mock_data "google_project" {
    defaults = {
      number = "123456789012"
    }
  }
  # Real-shaped values where the provider validates the shape of a computed attribute.
  mock_resource "google_service_account" {
    defaults = {
      name  = "projects/test-site-project/serviceAccounts/site-mocked@test-site-project.iam.gserviceaccount.com"
      email = "site-mocked@test-site-project.iam.gserviceaccount.com"
    }
  }
  mock_resource "google_iam_workload_identity_pool" {
    defaults = {
      name = "projects/123456789012/locations/global/workloadIdentityPools/github-actions"
    }
  }
}

mock_provider "google-beta" {}

# Mocked so these tests can never reach Cloudflare. dns.tf's production records are always
# planned; their own tests are in dns.tftest.hcl.
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

run "only_main_of_this_repository_can_deploy" {
  command = plan

  assert {
    condition     = strcontains(google_iam_workload_identity_pool_provider.github.attribute_condition, "assertion.ref == \"refs/heads/main\"")
    error_message = "WIF must only trust the main branch."
  }
  assert {
    condition     = strcontains(google_iam_workload_identity_pool_provider.github.attribute_condition, "assertion.repository_id == \"42\"")
    error_message = "WIF must pin the repository by numeric id."
  }
  assert {
    condition     = strcontains(google_iam_workload_identity_pool_provider.github.attribute_condition, "example/site/.github/workflows/hugo.yml@refs/heads/main")
    error_message = "WIF must name the deploy workflow on main."
  }
  assert {
    condition     = strcontains(google_iam_workload_identity_pool_provider.github.attribute_condition, "assertion.environment == \"cloud-run\"")
    error_message = "Only jobs in the cloud-run environment may federate."
  }
  assert {
    condition     = strcontains(google_iam_workload_identity_pool_provider.github.attribute_condition, "assertion.event_name in [\"push\", \"workflow_dispatch\", \"schedule\"]")
    error_message = "Only push, manual and scheduled runs may federate."
  }
  assert {
    condition     = length(regexall("workflows/", google_iam_workload_identity_pool_provider.github.attribute_condition)) == 2
    error_message = "Exactly two workflows (deploy and rollback) may federate."
  }
  assert {
    condition     = google_service_account_iam_member.github_becomes_deployer.member == "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/42"
    error_message = "Only identities from this repository id may become the deployer."
  }
}

run "deployer_is_scoped_to_one_service_and_one_repository" {
  command = plan

  assert {
    condition     = google_cloud_run_v2_service_iam_member.deployer_deploy.role == "roles/run.developer"
    error_message = "The deployer gets run.developer on the service only (it cannot change IAM)."
  }
  assert {
    condition     = google_artifact_registry_repository_iam_member.deployer_push.role == "roles/artifactregistry.writer"
    error_message = "The deployer can push to the one repository, nothing broader."
  }
}

run "service_scales_to_zero_and_is_public" {
  command = plan

  assert {
    condition     = google_cloud_run_v2_service.site.template[0].scaling[0].min_instance_count == 0
    error_message = "min instances above 0 costs money every hour."
  }
  assert {
    condition     = google_cloud_run_v2_service.site.template[0].containers[0].ports[0].container_port == 8080
    error_message = "The container port must match server/sws.toml."
  }
  assert {
    condition     = google_cloud_run_v2_service.site.ingress == "INGRESS_TRAFFIC_ALL"
    error_message = "The site must start open to all traffic (the cost cutoff closes it later; cloud-run.tf ignores ingress changes)."
  }
  assert {
    condition     = google_cloud_run_v2_service_iam_member.public.member == "allUsers"
    error_message = "It is a public website."
  }
}

# A named template makes a later template change resend that name, and Cloud Run refuses a
# second revision under an existing name (409). cloud-run.tf leaves the name to Cloud Run.
run "service_template_names_no_revision" {
  command = plan

  assert {
    condition     = google_cloud_run_v2_service.site.template[0].revision == null
    error_message = "The template must not name its revision; Cloud Run names each one (cloud-run.tf)."
  }
}

run "region_outside_domain_mapping_is_refused" {
  command = plan

  variables {
    region = "europe-west2"
  }

  expect_failures = [var.region]
}
