# Offline plan tests for the test name (test-domain.tf): `tofu test` from infra/.
#
# Providers are mocked: no GCP project, no Cloudflare account, no token. The point is the
# guard. kphutt.com and www.kphutt.com serve the live site, and no input may turn either of
# them into a record this configuration manages.

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
    }
  }
  mock_resource "google_iam_workload_identity_pool" {
    defaults = {
      name = "projects/123456789012/locations/global/workloadIdentityPools/github-actions"
    }
  }
  # The shape Google returns for a DNS_CNAME token: "<record name> <target>".
  mock_data "google_site_verification_token" {
    defaults = {
      token = "abc123def.next.kphutt.com gv-mocked.domainverify.googlehosted.com"
    }
  }
  # What the mapping asked for when next.kphutt.com was mapped with its own ownership
  # proven: eight address records.
  mock_resource "google_cloud_run_domain_mapping" {
    defaults = {
      status = [{
        conditions          = []
        observed_generation = 1
        mapped_route_name   = "site"
        resource_records = [
          { name = "next", type = "A", rrdata = "216.239.32.21" },
          { name = "next", type = "A", rrdata = "216.239.34.21" },
          { name = "next", type = "A", rrdata = "216.239.36.21" },
          { name = "next", type = "A", rrdata = "216.239.38.21" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:32::15" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:34::15" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:36::15" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:38::15" },
        ]
      }]
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
}

run "off_by_default_so_no_dns_and_no_token" {
  command = plan

  variables {
    cloudflare_zone_id = ""
  }

  assert {
    condition     = length(cloudflare_dns_record.test) == 0 && length(cloudflare_dns_record.verification) == 0
    error_message = "With test_domain_enabled unset, no DNS record may be planned."
  }
  assert {
    condition     = length(google_cloud_run_domain_mapping.test) == 0 && length(google_site_verification_web_resource.test) == 0
    error_message = "With test_domain_enabled unset, no mapping or ownership check may be planned."
  }
}

run "on_maps_only_the_test_name_dns_only" {
  command = plan

  variables {
    test_domain_enabled = true
  }

  assert {
    condition     = google_cloud_run_domain_mapping.test[0].name == "next.kphutt.com"
    error_message = "The mapping must be for the test name."
  }
  assert {
    condition     = google_cloud_run_domain_mapping.test[0].spec[0].route_name == google_cloud_run_v2_service.site.name
    error_message = "The mapping must point at the site's service."
  }
  assert {
    condition     = length(cloudflare_dns_record.test) == 8
    error_message = "The test name gets the eight address records the mapping asks for."
  }
  assert {
    condition     = alltrue([for r in values(cloudflare_dns_record.test) : r.name == "next.kphutt.com" && contains(["A", "AAAA"], r.type)])
    error_message = "Every record for the test name is an A or AAAA record named next.kphutt.com."
  }
  assert {
    condition = toset([for r in values(cloudflare_dns_record.test) : "${r.type} ${r.content}"]) == toset([
      "A 216.239.32.21", "A 216.239.34.21", "A 216.239.36.21", "A 216.239.38.21",
      "AAAA 2001:4860:4802:32::15", "AAAA 2001:4860:4802:34::15", "AAAA 2001:4860:4802:36::15", "AAAA 2001:4860:4802:38::15",
    ])
    error_message = "The test name's records are Google's address set."
  }
  assert {
    condition     = alltrue([for r in values(cloudflare_dns_record.test) : r.proxied == false && r.ttl == 300]) && cloudflare_dns_record.verification[0].proxied == false
    error_message = "Records are DNS only, never proxied."
  }
  assert {
    condition     = cloudflare_dns_record.verification[0].name == "abc123def.next.kphutt.com"
    error_message = "The ownership record sits under the test name."
  }
  assert {
    condition     = google_site_verification_web_resource.test[0].site[0].identifier == "next.kphutt.com"
    error_message = "Ownership is proven for the test name only, not for kphutt.com."
  }
  assert {
    condition = alltrue([
      for r in concat(values(cloudflare_dns_record.test), cloudflare_dns_record.verification) :
      !contains(["kphutt.com", "www.kphutt.com", "@"], lower(trimsuffix(r.name, ".")))
    ])
    error_message = "No managed record may be the apex or www."
  }
}

run "verification_can_be_left_to_a_manual_seed" {
  command = plan

  variables {
    test_domain_enabled  = true
    verify_test_hostname = false
  }

  # kphutt.com is the verified name here, so the test name is a subdomain of it. A
  # host name compares without regard to case.
  override_resource {
    target = google_cloud_run_domain_mapping.test[0]
    values = {
      status = [{
        conditions          = []
        observed_generation = 1
        mapped_route_name   = "site"
        resource_records = [
          { name = "next", type = "CNAME", rrdata = "GHS.googlehosted.com." },
        ]
      }]
    }
  }

  assert {
    condition     = length(cloudflare_dns_record.verification) == 0 && length(google_site_verification_web_resource.test) == 0
    error_message = "verify_test_hostname = false must plan no ownership record."
  }
  assert {
    condition     = length(google_cloud_run_domain_mapping.test) == 1 && length(cloudflare_dns_record.test) == 1
    error_message = "The mapping and its record are still planned."
  }
  assert {
    condition     = cloudflare_dns_record.test["cname"].name == "next.kphutt.com" && cloudflare_dns_record.test["cname"].type == "CNAME" && cloudflare_dns_record.test["cname"].content == "ghs.googlehosted.com"
    error_message = "A subdomain of a verified kphutt.com gets one CNAME to ghs.googlehosted.com."
  }
}

# The mapping's request must match the fixed list exactly. Each of these stops the run at
# the mapping, before any record for the test name.
run "a_mapping_asking_for_a_cname_is_refused" {
  command = plan

  variables {
    test_domain_enabled = true
  }

  override_resource {
    target = google_cloud_run_domain_mapping.test[0]
    values = {
      status = [{
        conditions          = []
        observed_generation = 1
        mapped_route_name   = "site"
        resource_records = [
          { name = "next", type = "CNAME", rrdata = "ghs.googlehosted.com." },
        ]
      }]
    }
  }

  expect_failures = [google_cloud_run_domain_mapping.test]
}

run "a_mapping_asking_for_a_different_address_is_refused" {
  command = plan

  variables {
    test_domain_enabled = true
  }

  override_resource {
    target = google_cloud_run_domain_mapping.test[0]
    values = {
      status = [{
        conditions          = []
        observed_generation = 1
        mapped_route_name   = "site"
        resource_records = [
          { name = "next", type = "A", rrdata = "216.239.32.21" },
          { name = "next", type = "A", rrdata = "216.239.34.21" },
          { name = "next", type = "A", rrdata = "216.239.36.21" },
          { name = "next", type = "A", rrdata = "198.51.100.7" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:32::15" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:34::15" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:36::15" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:38::15" },
        ]
      }]
    }
  }

  expect_failures = [google_cloud_run_domain_mapping.test]
}

run "a_mapping_asking_for_fewer_records_is_refused" {
  command = plan

  variables {
    test_domain_enabled = true
  }

  override_resource {
    target = google_cloud_run_domain_mapping.test[0]
    values = {
      status = [{
        conditions          = []
        observed_generation = 1
        mapped_route_name   = "site"
        resource_records = [
          { name = "next", type = "A", rrdata = "216.239.32.21" },
          { name = "next", type = "A", rrdata = "216.239.34.21" },
          { name = "next", type = "A", rrdata = "216.239.36.21" },
          { name = "next", type = "A", rrdata = "216.239.38.21" },
        ]
      }]
    }
  }

  expect_failures = [google_cloud_run_domain_mapping.test]
}

run "a_mapping_asking_for_an_extra_record_is_refused" {
  command = plan

  variables {
    test_domain_enabled = true
  }

  override_resource {
    target = google_cloud_run_domain_mapping.test[0]
    values = {
      status = [{
        conditions          = []
        observed_generation = 1
        mapped_route_name   = "site"
        resource_records = [
          { name = "next", type = "A", rrdata = "216.239.32.21" },
          { name = "next", type = "A", rrdata = "216.239.34.21" },
          { name = "next", type = "A", rrdata = "216.239.36.21" },
          { name = "next", type = "A", rrdata = "216.239.38.21" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:32::15" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:34::15" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:36::15" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:38::15" },
          { name = "next", type = "CNAME", rrdata = "ghs.googlehosted.com." },
        ]
      }]
    }
  }

  expect_failures = [google_cloud_run_domain_mapping.test]
}

run "a_subdomain_seed_with_address_records_is_refused" {
  command = plan

  variables {
    test_domain_enabled  = true
    verify_test_hostname = false
  }

  override_resource {
    target = google_cloud_run_domain_mapping.test[0]
    values = {
      status = [{
        conditions          = []
        observed_generation = 1
        mapped_route_name   = "site"
        resource_records = [
          { name = "next", type = "A", rrdata = "216.239.32.21" },
          { name = "next", type = "A", rrdata = "216.239.34.21" },
          { name = "next", type = "A", rrdata = "216.239.36.21" },
          { name = "next", type = "A", rrdata = "216.239.38.21" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:32::15" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:34::15" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:36::15" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:38::15" },
        ]
      }]
    }
  }

  expect_failures = [google_cloud_run_domain_mapping.test]
}

run "a_mapping_asking_for_a_record_twice_is_refused" {
  command = plan

  variables {
    test_domain_enabled = true
  }

  override_resource {
    target = google_cloud_run_domain_mapping.test[0]
    values = {
      status = [{
        conditions          = []
        observed_generation = 1
        mapped_route_name   = "site"
        resource_records = [
          { name = "next", type = "A", rrdata = "216.239.32.21" },
          { name = "next", type = "A", rrdata = "216.239.34.21" },
          { name = "next", type = "A", rrdata = "216.239.36.21" },
          { name = "next", type = "A", rrdata = "216.239.38.21" },
          { name = "next", type = "A", rrdata = "216.239.38.21" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:32::15" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:34::15" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:36::15" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:38::15" },
        ]
      }]
    }
  }

  expect_failures = [google_cloud_run_domain_mapping.test]
}

# Google's answer in its own order, with a trailing dot, still matches.
run "a_mapping_in_another_order_still_matches" {
  command = plan

  variables {
    test_domain_enabled = true
  }

  override_resource {
    target = google_cloud_run_domain_mapping.test[0]
    values = {
      status = [{
        conditions          = []
        observed_generation = 1
        mapped_route_name   = "site"
        resource_records = [
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:38::15" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:36::15" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:34::15" },
          { name = "next", type = "AAAA", rrdata = "2001:4860:4802:32::15" },
          { name = "next", type = "A", rrdata = "216.239.38.21" },
          { name = "next", type = "A", rrdata = "216.239.36.21" },
          { name = "next", type = "A", rrdata = "216.239.34.21" },
          { name = "next", type = "A", rrdata = "216.239.32.21." },
        ]
      }]
    }
  }

  assert {
    condition     = length(cloudflare_dns_record.test) == 8
    error_message = "The same set in another order is still the expected set."
  }
}

run "the_apex_is_refused" {
  command = plan

  variables {
    test_domain_enabled = true
    test_hostname       = "kphutt.com"
  }

  expect_failures = [var.test_hostname]
}

run "www_is_refused" {
  command = plan

  variables {
    test_domain_enabled = true
    test_hostname       = "www.kphutt.com"
  }

  expect_failures = [var.test_hostname]
}

run "the_apex_with_a_trailing_dot_is_refused" {
  command = plan

  variables {
    test_hostname = "kphutt.com."
  }

  expect_failures = [var.test_hostname]
}

run "www_in_capitals_is_refused" {
  command = plan

  variables {
    test_hostname = "WWW.kphutt.com"
  }

  expect_failures = [var.test_hostname]
}

run "a_name_outside_the_zone_is_refused" {
  command = plan

  variables {
    test_hostname = "next.kphutt.com.example.org"
  }

  expect_failures = [var.test_hostname]
}

run "a_lookalike_zone_is_refused" {
  command = plan

  variables {
    test_hostname = "next.notkphutt.com"
  }

  expect_failures = [var.test_hostname]
}

# Whatever name Google's token carries, the ownership record stays under the test name.
run "a_token_naming_the_apex_still_lands_under_the_test_name" {
  command = plan

  variables {
    test_domain_enabled = true
  }

  override_data {
    target = data.google_site_verification_token.test[0]
    values = {
      token = "kphutt.com. gv-mocked.domainverify.googlehosted.com"
    }
  }

  assert {
    condition     = cloudflare_dns_record.verification[0].name == "kphutt.com.next.kphutt.com"
    error_message = "A token naming the apex must not produce an apex record."
  }
}

run "on_without_a_zone_id_is_refused" {
  command = plan

  variables {
    test_domain_enabled = true
    cloudflare_zone_id  = ""
  }

  expect_failures = [
    cloudflare_dns_record.verification,
  ]
}
