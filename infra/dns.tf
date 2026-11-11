# The kphutt.com zone's production records at Cloudflare: the live site (the apex and www)
# and email (MX, SPF, DKIM, DMARC). The only file that may manage the apex or www.
#
# These records existed before OpenTofu did. The import blocks below adopted them as they
# were, so the first plan imported them and changed nothing. Every value is written out in
# full, so any change to a record is a line in this file and a diff in review. tests/dns.tftest.hcl repeats the values, so a change has
# to be made in both places, on purpose.
#
# Two resources, each with its own list, import block and lifecycle, because prevent_destroy
# covers a whole resource:
#
#   - cloudflare_dns_record.web: the apex and www (docs/design/cutover-runbook.md). Its
#     prevent_destroy is on. A rollback deletes the four AAAA records, so a rollback pull
#     request turns it off here and in scripts/check_infra.py.
#   - cloudflare_dns_record.mail: MX, SPF, DKIM and DMARC. Its prevent_destroy never comes off;
#     scripts/check_infra.py has no setting that allows it.
#
# The test name's records (next.kphutt.com and its ownership CNAME) are not here: they belong
# to test-domain.tf, and scripts/check_infra.py keeps the two files apart.
#
# A change to a record: edit its line, open a pull request, read the plan, apply by hand
# (infra/README.md, "Production DNS"). Removing a record takes two reviewed steps, because
# prevent_destroy refuses any plan that would delete one.

locals {
  # One line per record. id is Cloudflare's record id, used only to import; in the web list
  # only, it may be null for a record the next apply creates. ttl 1 means "automatic" at
  # Cloudflare. priority is set for MX records only. TXT values keep the quotes Cloudflare
  # stores them with.

  # The live site: the apex and www. Only A, AAAA and CNAME records, named kphutt.com or
  # www.kphutt.com.
  #
  # Cloud Run, by domain mapping: exactly the records the mappings in site-domain.tf ask for
  # (tests/dns.tftest.hcl checks they match). The four A records and www are the records
  # that pointed at GitHub Pages, changed in place, so they keep their ids and their keys
  # (named for the Pages addresses they held at import). The AAAA ids are written in too, so
  # a fresh state adopts the existing records instead of making copies.
  web_records = {
    "apex-a-108"   = { id = "55afe9d65f67573aabad45aa75d90068", name = "kphutt.com", type = "A", content = "216.239.32.21", priority = null }
    "apex-a-109"   = { id = "fa6a0db99be0d3e74bd79e7b5d88b9c5", name = "kphutt.com", type = "A", content = "216.239.34.21", priority = null }
    "apex-a-110"   = { id = "3047fb9505626052d92b6137b5bb8e96", name = "kphutt.com", type = "A", content = "216.239.36.21", priority = null }
    "apex-a-111"   = { id = "cf17f747d6448bf119074e194a5992ec", name = "kphutt.com", type = "A", content = "216.239.38.21", priority = null }
    "apex-aaaa-32" = { id = "c4a6733117dcc00bf07c8215d804d7db", name = "kphutt.com", type = "AAAA", content = "2001:4860:4802:32::15", priority = null }
    "apex-aaaa-34" = { id = "6bfa19e5ac014736d66ab63497a85152", name = "kphutt.com", type = "AAAA", content = "2001:4860:4802:34::15", priority = null }
    "apex-aaaa-36" = { id = "42ccef79bcd652b053ba3c6161059790", name = "kphutt.com", type = "AAAA", content = "2001:4860:4802:36::15", priority = null }
    "apex-aaaa-38" = { id = "4a0cfcfbe87b0fba2852ea1ef3f30f5b", name = "kphutt.com", type = "AAAA", content = "2001:4860:4802:38::15", priority = null }
    "www"          = { id = "3bd7a0279c94812f42f2b49583660110", name = "www.kphutt.com", type = "CNAME", content = "ghs.googlehosted.com", priority = null }
  }

  # Email. Only MX and TXT records.
  mail_records = {
    "mx-aspmx"        = { id = "c776340c6002a3766293ea4b552a91c8", name = "kphutt.com", type = "MX", content = "aspmx.l.google.com", priority = 10 }
    "mx-alt1"         = { id = "91435220b154ef8bec732f817c21c53a", name = "kphutt.com", type = "MX", content = "alt1.aspmx.l.google.com", priority = 20 }
    "mx-alt2"         = { id = "c89b644debba9a6f6778bbf7160f1ff8", name = "kphutt.com", type = "MX", content = "alt2.aspmx.l.google.com", priority = 20 }
    "mx-aspmx2"       = { id = "cca324e3d8b485ae8bc2249b5301fdd5", name = "kphutt.com", type = "MX", content = "aspmx2.googlemail.com", priority = 30 }
    "mx-aspmx3"       = { id = "27deb6ae0f58e789c3d14d4217f49ae8", name = "kphutt.com", type = "MX", content = "aspmx3.googlemail.com", priority = 30 }
    "mx-aspmx4"       = { id = "a97e1aa09a3e6eaa241ca6999009ad81", name = "kphutt.com", type = "MX", content = "aspmx4.googlemail.com", priority = 30 }
    "mx-aspmx5"       = { id = "334f99b0d7c6329a1adff55465751079", name = "kphutt.com", type = "MX", content = "aspmx5.googlemail.com", priority = 30 }
    "txt-spf"         = { id = "9541f5fc2da9db891a418258c5096156", name = "kphutt.com", type = "TXT", content = "\"v=spf1 include:_spf.google.com -all\"", priority = null }
    "txt-dmarc"       = { id = "d783b582368bd0baa9a777c981239dc3", name = "_dmarc.kphutt.com", type = "TXT", content = "\"v=DMARC1; p=reject;\"", priority = null }
    "txt-dkim-google" = { id = "03cbd62ccf21037e455b7c0ca7443966", name = "google._domainkey.kphutt.com", type = "TXT", content = "\"v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAjo4Mumk8p2DLXArMbS6ctGZmk/JsY/S+OhxxdIDi3whzvLg0m5AKxTKh49xDKOZPNrgwua+qtn43NSwmcovBON9SSN99MDSiHDVU8H41wAOBNKXpAEAK0oN2m9vMNjt1A0wOfbr9qr/rqUkNxSOoGvHxVPfrfllWa2jQP7LwCi3hvT4OwyqicoDkd1EZXOaG1\" \"kaKDsKvfZOwd5K1PKjviD61I3m2jqeZVYvMuI9LwOv5VbDal9I+HhjvRtKhnJVvspp+vaxAax7/9PnhtAi6b82Ef8xjdk+YmnRAsUUmblWyhJXAY7telLFQk0SYiEmfxyzG7rQcrB2mqM8OxNrAhwIDAQAB\"", priority = null }
  }
}

# Adopts each existing record into state. Once a record is in state, its import block does
# nothing; it stays so that a fresh state adopts the same records rather than creating copies.
# Only the offline tests turn it off (var.import_production_dns): `tofu test` cannot import.
# A web record written before it exists has id = null: it is skipped here and the apply
# creates it. The pull request after that apply writes its id in (docs/design/cutover-runbook.md).
import {
  for_each = var.import_production_dns ? { for k, v in local.web_records : k => v if v.id != null } : {}
  to       = cloudflare_dns_record.web[each.key]
  id       = "${var.cloudflare_zone_id}/${each.value.id}"
}

import {
  for_each = var.import_production_dns ? local.mail_records : {}
  to       = cloudflare_dns_record.mail[each.key]
  id       = "${var.cloudflare_zone_id}/${each.value.id}"
}

resource "cloudflare_dns_record" "web" {
  for_each = local.web_records
  zone_id  = var.cloudflare_zone_id
  name     = each.value.name
  type     = each.value.type
  content  = each.value.content
  priority = each.value.priority
  ttl      = 1
  # DNS only. infra/README.md and AGENTS.md explain why the proxy stays off.
  proxied = false

  lifecycle {
    # A rollback pull request sets this false, with WEB_PREVENT_DESTROY in
    # scripts/check_infra.py, and the re-lock pull request after it sets both back.
    prevent_destroy = true

    precondition {
      condition     = var.cloudflare_zone_id != ""
      error_message = "Set cloudflare_zone_id: the production records in dns.tf need it for every plan."
    }
  }
}

resource "cloudflare_dns_record" "mail" {
  for_each = local.mail_records
  zone_id  = var.cloudflare_zone_id
  name     = each.value.name
  type     = each.value.type
  content  = each.value.content
  priority = each.value.priority
  ttl      = 1
  proxied  = false

  lifecycle {
    # Never lifted. Losing a mail record loses mail.
    prevent_destroy = true

    precondition {
      condition     = var.cloudflare_zone_id != ""
      error_message = "Set cloudflare_zone_id: the production records in dns.tf need it for every plan."
    }
  }
}
