# Offline plan tests for the live names on Cloud Run (site-domain.tf): `tofu test` from infra/.
#
# Providers are mocked: no GCP project, no Cloudflare account, no token. The points are the
# guards: kphutt.com's ownership record can only land on one new name directly under the
# zone, the mappings must ask for exactly their fixed lists, and none of it changes a
# production record in dns.tf.

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


run "proves_kphutt_com_and_maps_both_names_without_moving_traffic" {
  command = plan

  assert {
    condition     = google_site_verification_web_resource.apex.site[0].identifier == "kphutt.com" && google_site_verification_web_resource.apex.site[0].type == "INET_DOMAIN" && google_site_verification_web_resource.apex.verification_method == "DNS_CNAME"
    error_message = "Ownership is proven for kphutt.com, by a CNAME."
  }
  assert {
    condition     = cloudflare_dns_record.apex_verification.name == "abc123def.kphutt.com" && cloudflare_dns_record.apex_verification.type == "CNAME" && cloudflare_dns_record.apex_verification.content == "gv-mockedapex.dv.googlehosted.com"
    error_message = "The ownership record is the token's CNAME, on the token's own name directly under the zone."
  }
  assert {
    condition     = cloudflare_dns_record.apex_verification.proxied == false && cloudflare_dns_record.apex_verification.ttl == 300
    error_message = "The ownership record is DNS only."
  }
  assert {
    condition     = google_cloud_run_domain_mapping.apex.name == "kphutt.com" && google_cloud_run_domain_mapping.www.name == "www.kphutt.com"
    error_message = "The two mappings are for kphutt.com and www.kphutt.com."
  }
  assert {
    condition     = google_cloud_run_domain_mapping.apex.spec[0].route_name == google_cloud_run_v2_service.site.name && google_cloud_run_domain_mapping.www.spec[0].route_name == google_cloud_run_v2_service.site.name
    error_message = "Both mappings point at the site's service."
  }
  assert {
    condition     = google_cloud_run_domain_mapping.apex.location == "us-west1" && google_cloud_run_domain_mapping.apex.metadata[0].namespace == "test-site-project" && google_cloud_run_domain_mapping.www.metadata[0].namespace == "test-site-project"
    error_message = "The mappings are in the site's project and region."
  }
  assert {
    condition     = length(cloudflare_dns_record.test) == 0 && length(google_cloud_run_domain_mapping.test) == 0
    error_message = "The live names add nothing for the test name."
  }
}

# Google may return the label with the zone and a trailing dot, and in capitals.
run "a_token_with_the_zone_name_still_lands_on_its_label" {
  command = plan

  override_data {
    target = data.google_site_verification_token.apex
    values = {
      token = "ABC123DEF.kphutt.com. gv-mockedapex.dv.googlehosted.com." # gitleaks:allow
    }
  }

  assert {
    condition     = cloudflare_dns_record.apex_verification.name == "abc123def.kphutt.com" && cloudflare_dns_record.apex_verification.content == "gv-mockedapex.dv.googlehosted.com"
    error_message = "The zone suffix, the trailing dots and the case are dropped, and the record keeps its label."
  }
}

# The test name and the live names together: four records outside dns.tf, none of them a
# production name.
run "with_the_test_name_on_the_names_stay_apart" {
  command = plan

  variables {
    test_domain_enabled = true
  }

  override_data {
    target = data.google_site_verification_token.test[0]
    values = {
      token = "xyz789ghi gv-mockedtest.dv.googlehosted.com"
    }
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

  assert {
    condition     = cloudflare_dns_record.verification[0].name == "xyz789ghi.next.kphutt.com" && cloudflare_dns_record.apex_verification.name == "abc123def.kphutt.com"
    error_message = "Each ownership record is on its own name."
  }
}

# www serves the live site.
run "a_token_naming_www_is_refused" {
  command = plan

  variables {
    apex_verification_expected = "www gv-mockedapex.dv.googlehosted.com"
  }

  override_data {
    target = data.google_site_verification_token.apex
    values = {
      token = "www gv-mockedapex.dv.googlehosted.com"
    }
  }

  expect_failures = [cloudflare_dns_record.apex_verification]
}

# The apex serves the live site.
run "a_token_naming_the_apex_is_refused" {
  command = plan

  variables {
    apex_verification_expected = "kphutt.com gv-mockedapex.dv.googlehosted.com"
  }

  override_data {
    target = data.google_site_verification_token.apex
    values = {
      token = "kphutt.com gv-mockedapex.dv.googlehosted.com"
    }
  }

  expect_failures = [cloudflare_dns_record.apex_verification]
}

# The apex serves the live site.
run "a_token_naming_the_apex_with_a_dot_is_refused" {
  command = plan

  variables {
    apex_verification_expected = "kphutt.com gv-mockedapex.dv.googlehosted.com"
  }

  override_data {
    target = data.google_site_verification_token.apex
    values = {
      token = "kphutt.com. gv-mockedapex.dv.googlehosted.com"
    }
  }

  expect_failures = [cloudflare_dns_record.apex_verification]
}

# _dmarc is mail's (dns.tf).
run "a_token_naming_a_mail_name_is_refused" {
  command = plan

  variables {
    apex_verification_expected = "_dmarc gv-mockedapex.dv.googlehosted.com"
  }

  override_data {
    target = data.google_site_verification_token.apex
    values = {
      token = "_dmarc gv-mockedapex.dv.googlehosted.com"
    }
  }

  expect_failures = [cloudflare_dns_record.apex_verification]
}

# Only one label directly under the zone.
run "a_token_naming_a_deeper_name_is_refused" {
  command = plan

  variables {
    apex_verification_expected = "google._domainkey gv-mockedapex.dv.googlehosted.com"
  }

  override_data {
    target = data.google_site_verification_token.apex
    values = {
      token = "google._domainkey gv-mockedapex.dv.googlehosted.com"
    }
  }

  expect_failures = [cloudflare_dns_record.apex_verification]
}

# next.kphutt.com belongs to test-domain.tf.
run "a_token_naming_the_test_name_is_refused" {
  command = plan

  variables {
    apex_verification_expected = "next gv-mockedapex.dv.googlehosted.com"
  }

  override_data {
    target = data.google_site_verification_token.apex
    values = {
      token = "next gv-mockedapex.dv.googlehosted.com"
    }
  }

  expect_failures = [cloudflare_dns_record.apex_verification]
}

# A DNS_CNAME token has a name and a target.
run "a_token_without_a_target_is_refused" {
  command = plan

  variables {
    apex_verification_expected = " "
  }

  override_data {
    target = data.google_site_verification_token.apex
    values = {
      token = "abc123def"
    }
  }

  expect_failures = [cloudflare_dns_record.apex_verification]
}

# The target must be Google's verification host.
run "a_token_pointing_elsewhere_is_refused" {
  command = plan

  variables {
    apex_verification_expected = "abc123def verify.example.com"
  }

  override_data {
    target = data.google_site_verification_token.apex
    values = {
      token = "abc123def verify.example.com"
    }
  }

  expect_failures = [cloudflare_dns_record.apex_verification]
}

# An empty label would be the apex.
run "a_token_with_an_empty_label_is_refused" {
  command = plan

  variables {
    apex_verification_expected = " gv-mockedapex.dv.googlehosted.com"
  }

  override_data {
    target = data.google_site_verification_token.apex
    values = {
      token = ". gv-mockedapex.dv.googlehosted.com"
    }
  }

  expect_failures = [cloudflare_dns_record.apex_verification]
}

# The apex cannot hold a CNAME.
run "an_apex_mapping_asking_for_a_cname_is_refused" {
  command = plan

  override_resource {
    target = google_cloud_run_domain_mapping.apex
    values = {
      status = [{
        conditions          = []
        observed_generation = 1
        mapped_route_name   = "site"
        resource_records = [
          { name = "", type = "CNAME", rrdata = "ghs.googlehosted.com." },
        ]
      }]
    }
  }

  expect_failures = [google_cloud_run_domain_mapping.apex]
}

# An address outside Google's set.
run "an_apex_mapping_asking_for_a_different_address_is_refused" {
  command = plan

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
          { name = "", type = "A", rrdata = "198.51.100.7" },
          { name = "", type = "AAAA", rrdata = "2001:4860:4802:32::15" },
          { name = "", type = "AAAA", rrdata = "2001:4860:4802:34::15" },
          { name = "", type = "AAAA", rrdata = "2001:4860:4802:36::15" },
          { name = "", type = "AAAA", rrdata = "2001:4860:4802:38::15" },
        ]
      }]
    }
  }

  expect_failures = [google_cloud_run_domain_mapping.apex]
}

# Only the A records.
run "an_apex_mapping_asking_for_fewer_records_is_refused" {
  command = plan

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
        ]
      }]
    }
  }

  expect_failures = [google_cloud_run_domain_mapping.apex]
}

# One record too many.
run "an_apex_mapping_asking_for_an_extra_record_is_refused" {
  command = plan

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
          { name = "", type = "CNAME", rrdata = "ghs.googlehosted.com." },
        ]
      }]
    }
  }

  expect_failures = [google_cloud_run_domain_mapping.apex]
}

# The same record twice.
run "an_apex_mapping_asking_for_a_record_twice_is_refused" {
  command = plan

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
          { name = "", type = "A", rrdata = "216.239.38.21" },
        ]
      }]
    }
  }

  expect_failures = [google_cloud_run_domain_mapping.apex]
}

# www gets a CNAME, not addresses.
run "a_www_mapping_asking_for_addresses_is_refused" {
  command = plan

  override_resource {
    target = google_cloud_run_domain_mapping.www
    values = {
      status = [{
        conditions          = []
        observed_generation = 1
        mapped_route_name   = "site"
        resource_records = [
          { name = "www", type = "A", rrdata = "216.239.32.21" },
          { name = "www", type = "A", rrdata = "216.239.34.21" },
          { name = "www", type = "A", rrdata = "216.239.36.21" },
          { name = "www", type = "A", rrdata = "216.239.38.21" },
          { name = "www", type = "AAAA", rrdata = "2001:4860:4802:32::15" },
          { name = "www", type = "AAAA", rrdata = "2001:4860:4802:34::15" },
          { name = "www", type = "AAAA", rrdata = "2001:4860:4802:36::15" },
          { name = "www", type = "AAAA", rrdata = "2001:4860:4802:38::15" },
        ]
      }]
    }
  }

  expect_failures = [google_cloud_run_domain_mapping.www]
}

# Only ghs.googlehosted.com.
run "a_www_mapping_asking_for_another_cname_is_refused" {
  command = plan

  override_resource {
    target = google_cloud_run_domain_mapping.www
    values = {
      status = [{
        conditions          = []
        observed_generation = 1
        mapped_route_name   = "site"
        resource_records = [
          { name = "www", type = "CNAME", rrdata = "ghs.example.com." },
        ]
      }]
    }
  }

  expect_failures = [google_cloud_run_domain_mapping.www]
}

# No record at all.
run "a_www_mapping_asking_for_nothing_is_refused" {
  command = plan

  override_resource {
    target = google_cloud_run_domain_mapping.www
    values = {
      status = [{
        conditions          = []
        observed_generation = 1
        mapped_route_name   = "site"
        resource_records = [

        ]
      }]
    }
  }

  expect_failures = [google_cloud_run_domain_mapping.www]
}

# Google's answer in its own order, with trailing dots and capitals, still matches.
run "mappings_in_another_order_still_match" {
  command = plan

  override_resource {
    target = google_cloud_run_domain_mapping.apex
    values = {
      status = [{
        conditions          = []
        observed_generation = 1
        mapped_route_name   = "site"
        resource_records = [
          { name = "", type = "AAAA", rrdata = "2001:4860:4802:38::15." },
          { name = "", type = "AAAA", rrdata = "2001:4860:4802:36::15." },
          { name = "", type = "AAAA", rrdata = "2001:4860:4802:34::15." },
          { name = "", type = "AAAA", rrdata = "2001:4860:4802:32::15." },
          { name = "", type = "A", rrdata = "216.239.38.21." },
          { name = "", type = "A", rrdata = "216.239.36.21." },
          { name = "", type = "A", rrdata = "216.239.34.21." },
          { name = "", type = "A", rrdata = "216.239.32.21." },
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
          { name = "www", type = "CNAME", rrdata = "GHS.googlehosted.com." },
        ]
      }]
    }
  }

  assert {
    condition     = google_cloud_run_domain_mapping.apex.name == "kphutt.com" && google_cloud_run_domain_mapping.www.name == "www.kphutt.com"
    error_message = "The same sets in another form still match."
  }
}

run "the_ownership_record_needs_a_zone_id" {
  command = plan

  variables {
    cloudflare_zone_id = ""
  }

  expect_failures = [cloudflare_dns_record.apex_verification, cloudflare_dns_record.web, cloudflare_dns_record.mail, cloudflare_dns_record.services]
}
