# `infra/`: the site's Google Cloud resources (OpenTofu)

One flat folder, no modules. State lives in a versioned GCS bucket in the same project.
The plan this implements is [docs/design/site-move.md](../docs/design/site-move.md).

| File | What it holds |
|---|---|
| `versions.tf` | OpenTofu and provider pins; the partial GCS backend |
| `variables.tf` | Inputs; account-specific ones have no default |
| `cloud-run.tf` | The Cloud Run service (min instances 0) and public access |
| `artifact-registry.tf` | The image repository, with a cleanup policy |
| `identities.tf` | The runtime and deployer service accounts, and Workload Identity Federation |
| `monitoring.tf` | The billing budget alert, and the request-log bucket, sink and exclusion |
| `dns.tf` | The zone's production DNS records at Cloudflare: the apex, `www`, mail. The only file that may manage them |
| `test-domain.tf` | The test name: domain mapping, its DNS record at Cloudflare, ownership proof. Off by default |
| `outputs.tf` | The values the deploy workflow needs as repository variables |
| `seed.py` | The one-time steps OpenTofu cannot do: the project, the GitHub environment, the repository variables |
| `tests/plan.tftest.hcl` | Offline plan tests with mocked providers |
| `tests/dns.tftest.hcl` | Offline tests that pin every production record's value |
| `tests/test-domain.tftest.hcl` | Offline tests that the test name can never be `kphutt.com` or `www` |

## Who changes what

- **OpenTofu owns the infrastructure.** Every change lands through a reviewed pull
  request, and is applied by hand from a dev machine. CI cannot: the deployer
  identity it gets can push an image and deploy a revision of this one service, and
  nothing else.
- **Deploying is trusted to `main`.** The Google identity accepts only the
  `cloud-run` environment's jobs in `hugo.yml` and `rollback.yml`, on `main`, from a push
  or a manual run. That is only as strong as the rule that nothing reaches `main` except
  through a reviewed pull request, so branch protection on `main` is part of this
  security boundary.
- **The deploy workflow owns the running image and the traffic split.** `cloud-run.tf`
  ignores both, so `tofu apply` never undoes a deploy or a rollback.

## Checks (offline, no GCP access)

```bash
cd infra
mise exec -- tofu fmt -check
mise exec -- tofu init -backend=false
mise exec -- tofu validate
mise exec -- tofu test
```

## First-time setup (once)

Every step is a command from this repository. The only things done outside it are signing
in and creating the Cloudflare token.

1. Copy `terraform.tfvars.example` to `terraform.tfvars` (git-ignored) and fill it in,
   including `cloudflare_zone_id`.
2. Sign in with an account that can create projects and use the billing account:
   `gcloud auth login` and `gcloud auth application-default login`.
3. `python infra/seed.py project` prints what it will do. Read it, then run it again with
   `--apply`. It creates the project, links billing, enables the APIs and creates the state
   bucket.
4. From `infra/`, with `CLOUDFLARE_API_TOKEN` set (every plan needs it, see "Production
   DNS"; the token is created as in "The test name", step 1):
   ```bash
   mise exec -- tofu init -backend-config="bucket=<project_id>-tofu-state" -backend-config="prefix=site"
   mise exec -- tofu plan -out=site.tfplan
   mise exec -- tofu apply site.tfplan
   ```
   This creates the service on Google's placeholder image, so `*.run.app` answers with a
   hello page until the first deploy.
5. `python infra/seed.py env --apply` creates the `cloud-run` environment, or updates it,
   so that only `main` can deploy to it. It needs `gh` signed in as a repository admin.
   (The first deploy would create the environment anyway, without that limit; the Google
   side refuses anything but `main` either way.)
6. `mise exec -- python infra/seed.py vars --apply` sets the six repository variables the
   deploy workflow reads. Until they exist, the Cloud Run deploy job skips itself, so the
   workflow keeps working for GitHub Pages.

## The test name (`next.kphutt.com`)

`test-domain.tf` maps the test name to the service (option A in
[the ADR](../docs/decisions/0001-custom-domain-on-cloud-run.md)) and creates its DNS record
at Cloudflare, DNS only. It is off until `test_domain_enabled = true`.

It manages new records under the test name and nothing else. It does not import or change
any existing record, and `kphutt.com` and `www.kphutt.com` cannot be managed from here (they
are in `dns.tf`, see "Production DNS"):
`test_hostname` refuses them, every record has a precondition that refuses them,
`scripts/check_infra.py` fails if a Cloudflare record could be named anything else, and
`tests/test-domain.tftest.hcl` proves the refusals. The token cannot enforce this, because
Cloudflare scopes tokens to a zone, not to single records. The code does.

Once, before turning it on:

1. Create a Cloudflare API token (My Profile, API Tokens, Create Custom Token):
   - Permissions: **Zone, DNS, Edit**. Nothing else.
   - Zone Resources: **Include, Specific zone, kphutt.com**.
   - Optional: an expiry date, and client IP filtering to your address.

   Keep it in the password manager and pass it to OpenTofu only as the environment
   variable `CLOUDFLARE_API_TOKEN` (for example from a secrets manager at run time). It never goes in
   `terraform.tfvars` or the repository.
2. Copy the zone id from the kphutt.com Overview page in Cloudflare into
   `cloudflare_zone_id` in `terraform.tfvars`. It is not a secret.
3. Proving ownership uses Google's Site Verification API, which needs its own OAuth scope.
   Sign in for OpenTofu once more, with that scope added:
   ```bash
   gcloud auth application-default login --scopes=openid,https://www.googleapis.com/auth/userinfo.email,https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/siteverification
   ```
   If the project was seeded before this API was added to `seed.py`, run
   `python infra/seed.py project --apply` again: it is safe to re-run and enables it.

Then set `test_domain_enabled = true` and plan and apply as in step 4. The apply:

1. asks Google for an ownership token for the test name and creates the CNAME it names,
   under the test name (`<token>.next.kphutt.com`);
2. has Google check it, which makes the account running OpenTofu a verified owner of the
   test name (not of `kphutt.com`);
3. creates the domain mapping, and checks that it asks for exactly the records in
   `local.test_records` (`test-domain.tf`). If it asks for anything else, the apply stops
   there, before any record for the test name exists;
4. creates those records under the test name, DNS only. Because the mapped name is the
   verified name, Google treats it like a bare domain: four A and four AAAA records, at
   Google's front-end addresses. Google then issues the certificate, usually within 15
   minutes and up to a day.

The records are a fixed list in the code, not values read from the mapping. OpenTofu cannot
plan one record per entry of a list it only learns during the apply. The check in step 3 is
what keeps the list honest. If Google ever changes what it asks for, the apply fails with a
message saying so; read the mapping with
`gcloud beta run domain-mappings describe --domain next.kphutt.com`, and change the list in
review.

Google's mapping page says to verify the parent domain, `kphutt.com`. Verifying only the
test name was enough: Cloud Run accepted the mapping. If that ever stops working, set
`verify_test_hostname = false`, verify `kphutt.com` once with
`gcloud domains verify kphutt.com` (Search Console asks for a TXT record on `kphutt.com`,
added by hand: that record is on the apex, so this code will not create it), and apply
again. The test name is then a subdomain of the verified name, so it gets one CNAME to
`ghs.googlehosted.com` instead.

To switch it off, set `test_domain_enabled = false` and apply: the test name's records, the
mapping and the ownership CNAME are removed. OpenTofu forgets Google's ownership record rather than
deleting it, because Google's API refuses to delete it while the CNAME still exists. Google
re-checks ownership from time to time and drops it once the CNAME is gone.

`kphutt.com` itself is not here. Its records are in `dns.tf`.

## Production DNS (`dns.tf`)

Every record in the kphutt.com zone, except the test name's, is in `dns.tf`, as two
resources:

- `cloudflare_dns_record.web` (list `local.web_records`): the four apex A records and the
  `www` CNAME that serve the live site (GitHub Pages, until the cutover). These are the
  records the cutover replaces.
- `cloudflare_dns_record.mail` (list `local.mail_records`): seven MX, and the SPF, DKIM and
  DMARC TXT records.

All are DNS only. They are two resources, not one, because `prevent_destroy` covers a whole
resource: the cutover has to unlock the web records without unlocking mail.

They existed before this code. Import blocks adopted them exactly as Cloudflare held them,
so the first plan said "15 to import, 0 to add, 0 to change, 0 to destroy". The import
blocks stay: once a record is in state they do nothing, and on a fresh state they adopt the
same records instead of creating copies. (`import_production_dns` turns them off for the
offline tests only, because `tofu test` cannot import. Leave it on.)

Because these records are always part of the configuration, **every plan now needs
`CLOUDFLARE_API_TOKEN` and `cloudflare_zone_id`**, not only when the test name is on. The
token is the same one as for the test name: Zone, DNS, Edit, on kphutt.com only. Cloudflare
cannot scope a token to single records, so that token can change any record in the zone;
the guards below are what keep a plan from doing so by accident.

How a change flows:

1. Edit the record's line in `dns.tf`, and the same value in `tests/dns.tftest.hcl`. The test
   repeats every value, so a change has to be made twice, on purpose.
2. Open a pull request. `python scripts/check_infra.py` runs the offline checks.
3. From `infra/`, with the token in the environment:
   `mise exec -- tofu plan -out=site.tfplan`. Read it: it must change only the records you
   meant to change.
4. `mise exec -- tofu apply site.tfplan`, by hand, after review.

Never change these records in the Cloudflare dashboard: the next plan would put them back.

The guards:

- `prevent_destroy` is on both resources, so any plan that would delete or replace a
  production record fails.
  - Mail: never lifted. `scripts/check_infra.py` requires `prevent_destroy = true` on
    `cloudflare_dns_record.mail` and has no setting that allows anything else. Deleting a
    mail record means changing that rule, in review.
  - Web: lifted only for the cutover, by one pull request that changes exactly two lines,
    `prevent_destroy = true` to `false` on `cloudflare_dns_record.web` in `dns.tf`, and
    `WEB_PREVENT_DESTROY = "true"` to `"false"` in `scripts/check_infra.py`. Either line alone
    fails the check. A later pull request sets both back (`docs/design/cutover-runbook.md`
    says when).
- `scripts/check_infra.py` fails unless `dns.tf` holds only these two resources, each over its
  own fixed list with one literal line per record and its own import block, DNS only,
  automatic TTL and `prevent_destroy` as above. The web list may hold only A, AAAA and CNAME
  records named `kphutt.com` or `www.kphutt.com`; the mail list only MX and TXT, so a mail
  record cannot be moved into the web list to unlock it. Only `dns.tf` may import a Cloudflare
  record, and no other file may use either list. The test name and anything under it are
  refused here, and every other file is still refused the apex and `www`.
- Nothing may loosen those pins: `dns.tf` may not use `ignore_changes` or
  `replace_triggered_by`, and no `moved` or `removed` block may be in `dns.tf` or name either
  resource from another file (either would take a record out from under its pins without a
  plan deleting it).
- `tests/dns.tftest.hcl` pins every name, type, value and MX priority.

## Cost

Fixed monthly cost is $0. Cloud Run scales to zero and its free tier covers this site's
traffic many times over; images and logs stay inside their free allowances (the image
repository keeps the 20 newest images, and request logs are kept 30 days). The budget alert
emails at 10%, 50% and 100% of `monthly_budget_usd` (default $1), and when the month's
forecast crosses it.
