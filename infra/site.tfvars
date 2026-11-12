# The settings every plan of this site uses. Committed and reviewed like the code, so every
# machine plans the same thing and nothing lives only on one PC. Nothing here is secret or
# personal: those values (the alert email, the billing account) are read from 1Password at run
# time, as listed in infra-apply.toml [secrets]. scripts/apply_infra.py passes this file with
# -var-file. Change a value here in a pull request, then apply (infra/README.md).

project_id           = "kphutt-site" # the site's own GCP project
region               = "us-west1"    # us-west1 or us-central1
github_repository    = "kphutt/kphutt.github.io"
github_repository_id = "1170454764" # gh api repos/kphutt/kphutt.github.io --jq .id

# The kphutt.com zone (dns.tf). Not a secret; it is on the zone's Overview page in Cloudflare.
# The API token is never here: it comes from 1Password as CLOUDFLARE_API_TOKEN.
cloudflare_zone_id = "c774a9abc0057fce9191cdc92d30147c"

# The test name next.kphutt.com (test-domain.tf). Off. See infra/README.md, "The test name".
test_domain_enabled = false
