# The live names on Cloud Run: kphutt.com and www.kphutt.com, option A in
# docs/decisions/0001-custom-domain-on-cloud-run.md. Step 5 of docs/design/cutover-runbook.md.
#
# This file moves no traffic. It creates, in order:
#
#   1. a CNAME that proves to Google we own kphutt.com, on its own random name directly
#      under the zone (<token>.kphutt.com). It is a new name, so it shares nothing with the
#      apex, www, mail or the test name;
#   2. the Google ownership record that checks it (Site Verification);
#   3. the domain mappings for kphutt.com and www.kphutt.com.
#
# A mapping serves a name only while that name's DNS points at Google. The apex and www
# records that point there are in dns.tf, and only dns.tf may change them. Google issues and
# renews each name's certificate only while its DNS points here.
#
# A postcondition on each mapping checks that Google asks for exactly the records in its fixed list below.
# dns.tf holds those same values, and tests/dns.tftest.hcl checks that the two match.

locals {
  # A DNS_CNAME token for kphutt.com is "<label> <target>", for example
  # "abc123def gv-abc123def.dv.googlehosted.com". The record goes at
  # <label>.kphutt.com whatever Google returns: a suffix of the zone name is dropped, and the
  # label must be one plain DNS label (the precondition below), so the record can never be
  # the apex, www, a mail name such as _dmarc, or anything under another name.
  apex_verification_parts       = split(" ", trimspace(data.google_site_verification_token.apex.token))
  apex_verification_label       = length(local.apex_verification_parts) == 2 ? trimsuffix(lower(trimsuffix(local.apex_verification_parts[0], ".")), ".${local.zone_name}") : ""
  apex_verification_record_name = "${local.apex_verification_label}.${local.zone_name}"
  apex_verification_target      = length(local.apex_verification_parts) == 2 ? lower(trimsuffix(local.apex_verification_parts[1], ".")) : ""
  apex_verification_label_ok    = can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", local.apex_verification_label)) && local.apex_verification_label != "www" && local.apex_verification_record_name != var.test_hostname
  apex_verification_target_ok   = can(regex("^[a-z0-9-]+[.]dv[.]googlehosted[.]com$", local.apex_verification_target))
  apex_verification_is_pinned   = "${local.apex_verification_label} ${local.apex_verification_target}" == var.apex_verification_expected

  # What the apex mapping asks for: Google's front-end addresses, the same set the test name
  # got (a mapped name that is itself the verified name is treated as a bare domain).
  apex_mapping_records = {
    "a-32"    = { type = "A", content = "216.239.32.21" }
    "a-34"    = { type = "A", content = "216.239.34.21" }
    "a-36"    = { type = "A", content = "216.239.36.21" }
    "a-38"    = { type = "A", content = "216.239.38.21" }
    "aaaa-32" = { type = "AAAA", content = "2001:4860:4802:32::15" }
    "aaaa-34" = { type = "AAAA", content = "2001:4860:4802:34::15" }
    "aaaa-36" = { type = "AAAA", content = "2001:4860:4802:36::15" }
    "aaaa-38" = { type = "AAAA", content = "2001:4860:4802:38::15" }
  }

  # What the www mapping asks for: www is a subdomain of the verified kphutt.com, so one CNAME.
  www_mapping_records = {
    "cname" = { type = "CNAME", content = "ghs.googlehosted.com" }
  }

  apex_mapping_expected = toset([for r in values(local.apex_mapping_records) : "${r.type} ${lower(r.content)}"])
  www_mapping_expected  = toset([for r in values(local.www_mapping_records) : "${r.type} ${lower(r.content)}"])
}

# 1. Proof of ownership of kphutt.com. DNS_CNAME, not a TXT record on the apex: the apex TXT
#    records are mail's (SPF) and belong to dns.tf.
data "google_site_verification_token" "apex" {
  type                = "INET_DOMAIN"
  identifier          = local.zone_name
  verification_method = "DNS_CNAME"
}

resource "cloudflare_dns_record" "apex_verification" {
  zone_id = var.cloudflare_zone_id
  name    = local.apex_verification_record_name
  type    = "CNAME"
  content = local.apex_verification_target
  ttl     = 300
  proxied = false
  comment = "Google ownership check for kphutt.com. Managed by OpenTofu."

  lifecycle {
    # Google re-checks ownership from time to time. Losing it would stop the mappings below
    # from being managed, so deleting this record takes a reviewed change to this line.
    prevent_destroy = true

    precondition {
      condition     = var.cloudflare_zone_id != ""
      error_message = "Set cloudflare_zone_id: the ownership record for kphutt.com needs it."
    }
    precondition {
      condition     = length(local.apex_verification_parts) == 2 && local.apex_verification_label_ok && local.apex_verification_target_ok
      error_message = "Google's DNS_CNAME token for kphutt.com was not \"<one plain label> <name>.dv.googlehosted.com\", or its label is www or the test name."
    }
    # Google returns a different token to each Google account, and an in-place update of this
    # record would remove the first account's proof. So the token is pinned in code.
    precondition {
      condition     = local.apex_verification_is_pinned
      error_message = "Google's ownership token for kphutt.com is not the pinned one (apex_verification_expected). It came from a different Google account than the one that set this up. Run OpenTofu as that account. Change the pin only in a reviewed change."
    }
    precondition {
      condition     = lookup(local.allowed_record_name, local.apex_verification_record_name, false)
      error_message = "Refusing a DNS record named ${local.apex_verification_record_name}: never kphutt.com or www."
    }
  }
}

# 2. Google checks the record and records the identity running tofu as an owner of
#    kphutt.com. ABANDON on destroy, as for the test name (test-domain.tf).
resource "google_site_verification_web_resource" "apex" {
  site {
    type       = data.google_site_verification_token.apex.type
    identifier = data.google_site_verification_token.apex.identifier
  }
  verification_method = data.google_site_verification_token.apex.verification_method
  deletion_policy     = "ABANDON"

  depends_on = [cloudflare_dns_record.apex_verification]
}

# 3. The mappings. DNS points at them, so deleting one takes that name off the internet.
#    Neither can be deleted through OpenTofu while prevent_destroy is set, and removing it
#    is a change to review.
resource "google_cloud_run_domain_mapping" "apex" {
  location = var.region
  name     = local.zone_name

  metadata {
    namespace = var.project_id
  }

  spec {
    route_name = google_cloud_run_v2_service.site.name
  }

  depends_on = [google_site_verification_web_resource.apex]

  lifecycle {
    prevent_destroy = true

    postcondition {
      condition = try(
        length(self.status[0].resource_records) == length(local.apex_mapping_records)
        && toset([for r in self.status[0].resource_records : "${r.type} ${lower(trimsuffix(r.rrdata, "."))}"]) == local.apex_mapping_expected,
        false
      )
      error_message = "The kphutt.com mapping asked for DNS records other than local.apex_mapping_records (site-domain.tf). Read them with `gcloud beta run domain-mappings describe --domain kphutt.com` and change that list, and dns.tf's at the cutover, in review."
    }
  }
}

resource "google_cloud_run_domain_mapping" "www" {
  location = var.region
  name     = "www.${local.zone_name}"

  metadata {
    namespace = var.project_id
  }

  spec {
    route_name = google_cloud_run_v2_service.site.name
  }

  depends_on = [google_site_verification_web_resource.apex]

  lifecycle {
    prevent_destroy = true

    postcondition {
      condition = try(
        length(self.status[0].resource_records) == length(local.www_mapping_records)
        && toset([for r in self.status[0].resource_records : "${r.type} ${lower(trimsuffix(r.rrdata, "."))}"]) == local.www_mapping_expected,
        false
      )
      error_message = "The www.kphutt.com mapping asked for DNS records other than local.www_mapping_records (site-domain.tf). Read them with `gcloud beta run domain-mappings describe --domain www.kphutt.com` and change that list, and dns.tf's at the cutover, in review."
    }
  }
}
