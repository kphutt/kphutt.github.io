# Offline plan tests for the production records (dns.tf): `tofu test` from infra/.
# prevent_destroy cannot be seen in a plan, so scripts/check_infra.py pins it instead.
#
# Providers are mocked: no Cloudflare account, no token. These tests repeat every production
# value on purpose. A change to the live site's DNS or to email has to be made here as well as
# in dns.tf, so it can only happen as a deliberate, reviewed edit to both.

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

variables {
  project_id           = "test-site-project"
  region               = "us-west1"
  github_repository    = "example/site"
  github_repository_id = "42"
  billing_account      = "000000-000000-000000"
  alert_email          = "alerts@example.com"
  cloudflare_zone_id   = "0123456789abcdef0123456789abcdef"
  # `tofu test` cannot run import blocks.
  import_production_dns = false
}

run "the_production_records_are_exactly_these" {
  command = plan

  assert {
    condition     = length(cloudflare_dns_record.web) == 5 && length(cloudflare_dns_record.mail) == 10
    error_message = "The zone has 15 production records: web (4 apex A, www) and mail (7 MX, 3 TXT)."
  }
  assert {
    condition = toset([for r in values(cloudflare_dns_record.web) : "${r.name} ${r.type} ${r.content}"]) == toset([
      "kphutt.com A 185.199.108.153",
      "kphutt.com A 185.199.109.153",
      "kphutt.com A 185.199.110.153",
      "kphutt.com A 185.199.111.153",
      "www.kphutt.com CNAME kphutt.github.io",
    ])
    error_message = "A web record's name, type or value changed. Change it here and in dns.tf, in review."
  }
  assert {
    condition = toset([for r in values(cloudflare_dns_record.mail) : "${r.name} ${r.type} ${r.content}"]) == toset([
      "kphutt.com MX aspmx.l.google.com",
      "kphutt.com MX alt1.aspmx.l.google.com",
      "kphutt.com MX alt2.aspmx.l.google.com",
      "kphutt.com MX aspmx2.googlemail.com",
      "kphutt.com MX aspmx3.googlemail.com",
      "kphutt.com MX aspmx4.googlemail.com",
      "kphutt.com MX aspmx5.googlemail.com",
      "kphutt.com TXT \"v=spf1 include:_spf.google.com -all\"",
      "_dmarc.kphutt.com TXT \"v=DMARC1; p=reject;\"",
      "google._domainkey.kphutt.com TXT \"v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAjo4Mumk8p2DLXArMbS6ctGZmk/JsY/S+OhxxdIDi3whzvLg0m5AKxTKh49xDKOZPNrgwua+qtn43NSwmcovBON9SSN99MDSiHDVU8H41wAOBNKXpAEAK0oN2m9vMNjt1A0wOfbr9qr/rqUkNxSOoGvHxVPfrfllWa2jQP7LwCi3hvT4OwyqicoDkd1EZXOaG1\" \"kaKDsKvfZOwd5K1PKjviD61I3m2jqeZVYvMuI9LwOv5VbDal9I+HhjvRtKhnJVvspp+vaxAax7/9PnhtAi6b82Ef8xjdk+YmnRAsUUmblWyhJXAY7telLFQk0SYiEmfxyzG7rQcrB2mqM8OxNrAhwIDAQAB\"",
    ])
    error_message = "A mail record's name, type or value changed. Change it here and in dns.tf, in review."
  }
  assert {
    condition = {
      for k, r in cloudflare_dns_record.mail : k => r.priority if r.type == "MX"
      } == {
      "mx-aspmx"  = 10
      "mx-alt1"   = 20
      "mx-alt2"   = 20
      "mx-aspmx2" = 30
      "mx-aspmx3" = 30
      "mx-aspmx4" = 30
      "mx-aspmx5" = 30
    }
    error_message = "The MX priorities changed."
  }
  assert {
    condition     = alltrue([for r in concat(values(cloudflare_dns_record.web), values(cloudflare_dns_record.mail)) : r.priority == null if r.type != "MX"])
    error_message = "Only MX records have a priority."
  }
  assert {
    condition     = alltrue([for r in concat(values(cloudflare_dns_record.web), values(cloudflare_dns_record.mail)) : r.proxied == false && r.ttl == 1])
    error_message = "Production records are DNS only, with Cloudflare's automatic TTL."
  }
  assert {
    condition     = alltrue([for r in concat(values(cloudflare_dns_record.web), values(cloudflare_dns_record.mail)) : r.zone_id == var.cloudflare_zone_id])
    error_message = "Production records are in the kphutt.com zone."
  }
  assert {
    condition     = alltrue([for r in concat(values(cloudflare_dns_record.web), values(cloudflare_dns_record.mail)) : r.name != "next.kphutt.com" && !endswith(r.name, ".next.kphutt.com")])
    error_message = "The test name's records belong to test-domain.tf, not dns.tf."
  }
}

# The production records exist whether or not the test name is on, and the two sets never share
# a record.
run "the_test_name_does_not_change_the_production_records" {
  command = plan

  variables {
    test_domain_enabled = true
  }

  override_data {
    target = data.google_site_verification_token.test[0]
    values = {
      token = "abc123def.next.kphutt.com gv-mocked.domainverify.googlehosted.com"
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
    condition     = length(cloudflare_dns_record.web) == 5 && length(cloudflare_dns_record.mail) == 10 && length(cloudflare_dns_record.test) == 8
    error_message = "Turning the test name on adds its records and leaves the production records as they are."
  }
}

run "production_records_need_a_zone_id" {
  command = plan

  variables {
    cloudflare_zone_id = ""
  }

  expect_failures = [cloudflare_dns_record.web, cloudflare_dns_record.mail, cloudflare_dns_record.apex_verification]
}
