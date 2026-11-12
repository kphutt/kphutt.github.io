# Offline plan tests for the pinned ownership token (site-domain.tf): `tofu test` from infra/.
#
# Google returns a different token to each Google account, and the ownership record would be
# updated in place. These runs leave apex_verification_expected at its default, so they pin
# the live values in variables.tf. A plan from another account must stop with an error.

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

mock_provider "cloudflare" {}

# kphutt.com's ownership token and mappings (site-domain.tf), in the shape Google returns:
# the token is "<label> <target>", the apex mapping asks for Google's eight addresses and
# www for one CNAME. The runs below replace them to test the guards.
override_data {
  target = data.google_site_verification_token.apex
  values = {
    token = "ivmjabtw4d5a gv-eq4qbtmh2utczh.dv.googlehosted.com"
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


run "the_live_token_plans_fine" {
  command = plan

  assert {
    condition     = cloudflare_dns_record.apex_verification.name == "ivmjabtw4d5a.kphutt.com" && cloudflare_dns_record.apex_verification.content == "gv-eq4qbtmh2utczh.dv.googlehosted.com"
    error_message = "The pinned token plans the live ownership record."
  }
}

# Another Google account gets another label.
run "a_token_with_another_label_is_refused" {
  command = plan

  override_data {
    target = data.google_site_verification_token.apex
    values = {
      token = "zzzzzzzzzzzz gv-eq4qbtmh2utczh.dv.googlehosted.com"
    }
  }

  expect_failures = [cloudflare_dns_record.apex_verification]
}

# Another Google account gets another target.
run "a_token_with_another_target_is_refused" {
  command = plan

  override_data {
    target = data.google_site_verification_token.apex
    values = {
      token = "ivmjabtw4d5a gv-zzzzzzzzzzzzzz.dv.googlehosted.com"
    }
  }

  expect_failures = [cloudflare_dns_record.apex_verification]
}
