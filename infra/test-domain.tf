# The test name for the move: option A in docs/decisions/0001-custom-domain-on-cloud-run.md,
# a Cloud Run domain mapping with a DNS-only record at Cloudflare. Step 4 of
# docs/design/cutover-runbook.md.
#
# Off unless test_domain_enabled is true. When on, it creates, in order:
#
#   1. a CNAME under the test name that proves to Google we own that name;
#   2. the Google ownership record that checks it (Site Verification);
#   3. the domain mapping from the test name to the service;
#   4. the test name's own records: the A and AAAA records the mapping asks for.
#
# The records in step 4 are a fixed list, written below, not values read back from the
# mapping. A list read from the mapping is only known after apply, and OpenTofu cannot plan
# one record per entry of an unknown list. The mapping is checked against the fixed list
# before any of the records is created.
#
# Nothing here imports or changes an existing record. kphutt.com and www.kphutt.com serve the
# live site and are managed in dns.tf only, never from this file: every DNS record below has a
# precondition that refuses them, test_hostname's validation refuses them, and
# scripts/check_infra.py fails if a Cloudflare record here could be given any other name.

locals {
  zone_name = "kphutt.com"

  # Never managed from here, in any form (dns.tf holds them).
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

  # What the mapping asks for when the mapped name is the verified name, as here: Google's
  # front-end addresses, the same set as on Google's page for pointing a bare domain at
  # Google (https://knowledge.workspace.google.com/admin/domains/configure-a-records).
  # Cloud Run's own page (https://cloud.google.com/run/docs/mapping-custom-domains) says to
  # copy what the mapping shows; for next.kphutt.com it showed exactly these.
  test_address_records = {
    "a-32"    = { type = "A", content = "216.239.32.21" }
    "a-34"    = { type = "A", content = "216.239.34.21" }
    "a-36"    = { type = "A", content = "216.239.36.21" }
    "a-38"    = { type = "A", content = "216.239.38.21" }
    "aaaa-32" = { type = "AAAA", content = "2001:4860:4802:32::15" }
    "aaaa-34" = { type = "AAAA", content = "2001:4860:4802:34::15" }
    "aaaa-36" = { type = "AAAA", content = "2001:4860:4802:36::15" }
    "aaaa-38" = { type = "AAAA", content = "2001:4860:4802:38::15" }
  }

  # When ownership is proven for kphutt.com instead (verify_test_hostname = false), the test
  # name is a subdomain of the verified name, and the mapping asks for one CNAME.
  test_subdomain_records = {
    "cname" = { type = "CNAME", content = "ghs.googlehosted.com" }
  }

  test_records = local.verify ? local.test_address_records : local.test_subdomain_records

  # The mapping's request and the list above, as "<type> <value>" strings, compared as sets.
  test_records_expected = toset([for r in values(local.test_records) : "${r.type} ${lower(r.content)}"])
}

# The names a Cloudflare record outside dns.tf may have: the test name's (this file) and the
# kphutt.com ownership record's (site-domain.tf). A precondition on each record calls this
# check. A name must be under kphutt.com, and must not be the apex or www.
locals {
  allowed_record_name = {
    for name in distinct(compact([var.test_hostname, local.verify ? local.verification_record_name : "", local.apex_verification_record_name])) :
    name => (
      endswith(lower(trimsuffix(name, ".")), ".${local.zone_name}")
      && !contains(local.protected_names, lower(trimsuffix(name, ".")))
      && !startswith(name, "@")
    )
  }
}

# 1. Proof of ownership of the test name only (not of kphutt.com). The DNS_CNAME method puts
#    the proof on its own name under the test name, so it never shares a name with the test
#    name's records.
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
    # The mapping must ask for exactly the records in local.test_records, no more and no
    # fewer. Anything else stops the run here, before any of the test name's records is
    # created (they depend on this resource).
    postcondition {
      condition = try(
        length(self.status[0].resource_records) == length(local.test_records)
        && toset([for r in self.status[0].resource_records : "${r.type} ${lower(trimsuffix(r.rrdata, "."))}"]) == local.test_records_expected,
        false
      )
      error_message = "The mapping asked for DNS records other than the ones in local.test_records (test-domain.tf). Read them with `gcloud beta run domain-mappings describe` and change that list in review; no record for the test name was created."
    }
  }
}

# 4. The test name itself, DNS only: one record per entry of local.test_records. Not
#    proxied: the ADR explains why a proxy in front of a mapping is not recommended.
resource "cloudflare_dns_record" "test" {
  for_each = local.test_domain ? local.test_records : {}
  zone_id  = var.cloudflare_zone_id
  name     = var.test_hostname
  type     = each.value.type
  content  = each.value.content
  ttl      = 300
  proxied  = false
  comment  = "Test name for the Cloud Run move. Managed by OpenTofu."

  # The values are fixed, so nothing ties these records to the mapping except this. It is
  # what makes the mapping's check run first.
  depends_on = [google_cloud_run_domain_mapping.test]

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
