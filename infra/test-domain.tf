# The test name for the move: option A in docs/decisions/0001-custom-domain-on-cloud-run.md,
# a Cloud Run domain mapping with a DNS-only record at Cloudflare. Step 4 of
# docs/design/cutover-runbook.md.
#
# Off unless test_domain_enabled is true. When on, it creates, in order:
#
#   1. a CNAME under the test name that proves to Google we own that name;
#   2. the Google ownership record that checks it (Site Verification);
#   3. the domain mapping from the test name to the service;
#   4. the test name's own CNAME, to the target the mapping gives.
#
# Nothing here imports or changes an existing record. kphutt.com and www.kphutt.com serve the
# live site and are never managed from this repository: every DNS record below has a
# precondition that refuses them, test_hostname's validation refuses them, and
# scripts/check_infra.py fails if a Cloudflare record could be given any other name.

locals {
  zone_name = "kphutt.com"

  # Never managed from here, in any form.
  protected_names = ["kphutt.com", "www.kphutt.com"]

  test_domain = var.test_domain_enabled
  verify      = var.test_domain_enabled && var.verify_test_hostname

  # A DNS_CNAME token is "<record name> <target>". The name is put under the test name
  # whatever Google returns, so this record can never land on the apex or www.
  verification_parts = local.verify ? split(" ", trimspace(data.google_site_verification_token.test[0].token)) : []
  verification_label = local.verify && length(local.verification_parts) == 2 ? lower(trimsuffix(local.verification_parts[0], ".")) : ""
  verification_record_name = (
    endswith(local.verification_label, ".${var.test_hostname}")
    ? local.verification_label
    : "${local.verification_label}.${var.test_hostname}"
  )
  verification_target = local.verify && length(local.verification_parts) == 2 ? trimsuffix(local.verification_parts[1], ".") : ""
}

# The names a Cloudflare record may have. A precondition on each record calls this check.
# A name must be under kphutt.com, and must not be the apex or www.
locals {
  allowed_record_name = {
    for name in compact([var.test_hostname, local.verify ? local.verification_record_name : ""]) :
    name => (
      endswith(lower(trimsuffix(name, ".")), ".${local.zone_name}")
      && !contains(local.protected_names, lower(trimsuffix(name, ".")))
      && !startswith(name, "@")
    )
  }
}

# 1. Proof of ownership of the test name only (not of kphutt.com). Google gives a CNAME,
#    not a TXT record, because a TXT record cannot share a name with the test name's CNAME.
data "google_site_verification_token" "test" {
  count               = local.verify ? 1 : 0
  type                = "INET_DOMAIN"
  identifier          = var.test_hostname
  verification_method = "DNS_CNAME"
}

resource "cloudflare_dns_record" "verification" {
  count   = local.verify ? 1 : 0
  zone_id = var.cloudflare_zone_id
  name    = local.verification_record_name
  type    = "CNAME"
  content = local.verification_target
  ttl     = 300
  proxied = false
  comment = "Google ownership check for the test name. Managed by OpenTofu."

  lifecycle {
    precondition {
      condition     = var.cloudflare_zone_id != ""
      error_message = "Set cloudflare_zone_id when test_domain_enabled is true."
    }
    precondition {
      condition     = length(local.verification_parts) == 2 && local.verification_label != "" && local.verification_target != ""
      error_message = "Google's DNS_CNAME token was not \"<name> <target>\"."
    }
    precondition {
      condition     = lookup(local.allowed_record_name, local.verification_record_name, false)
      error_message = "Refusing a DNS record named ${local.verification_record_name}: only names under the test name are managed here, never kphutt.com or www."
    }
  }
}

# 2. Google checks the record and records the identity running tofu as an owner of the
#    test name. ABANDON on destroy: the API refuses to delete the ownership while the record
#    still exists, and the record is removed in the same run. Google drops the ownership by
#    itself once the record is gone.
resource "google_site_verification_web_resource" "test" {
  count = local.verify ? 1 : 0

  site {
    type       = data.google_site_verification_token.test[0].type
    identifier = data.google_site_verification_token.test[0].identifier
  }
  verification_method = data.google_site_verification_token.test[0].verification_method
  deletion_policy     = "ABANDON"

  depends_on = [cloudflare_dns_record.verification]
}

# 3. The mapping. Cloud Run issues and renews its certificate once the record below exists.
resource "google_cloud_run_domain_mapping" "test" {
  count    = local.test_domain ? 1 : 0
  location = var.region
  name     = var.test_hostname

  metadata {
    namespace = var.project_id
  }

  spec {
    route_name = google_cloud_run_v2_service.site.name
  }

  depends_on = [google_site_verification_web_resource.test]

  lifecycle {
    precondition {
      condition     = var.cloudflare_zone_id != ""
      error_message = "Set cloudflare_zone_id when test_domain_enabled is true."
    }
    # A subdomain mapping asks for exactly one CNAME. Anything else (A or AAAA records)
    # means the name is not what this file expects, so stop before DNS changes.
    postcondition {
      condition     = try(length(self.status[0].resource_records) == 1 && self.status[0].resource_records[0].type == "CNAME", false)
      error_message = "The mapping asked for records other than one CNAME; this file only handles a subdomain."
    }
  }
}

# 4. The test name itself, DNS only. Not proxied: the ADR explains why a proxy in front of
#    a mapping is not recommended.
resource "cloudflare_dns_record" "test" {
  count   = local.test_domain ? 1 : 0
  zone_id = var.cloudflare_zone_id
  name    = var.test_hostname
  type    = "CNAME"
  content = trimsuffix(try(google_cloud_run_domain_mapping.test[0].status[0].resource_records[0].rrdata, ""), ".")
  ttl     = 300
  proxied = false
  comment = "Test name for the Cloud Run move. Managed by OpenTofu."

  lifecycle {
    precondition {
      condition     = var.cloudflare_zone_id != ""
      error_message = "Set cloudflare_zone_id when test_domain_enabled is true."
    }
    precondition {
      condition     = lookup(local.allowed_record_name, var.test_hostname, false)
      error_message = "Refusing a DNS record named ${var.test_hostname}: never kphutt.com or www."
    }
  }
}
