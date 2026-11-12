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
| `site-domain.tf` | The live names: ownership proof for `kphutt.com`, and the mappings for `kphutt.com` and `www` |
| `explorables.tf` | The explorables service and its mapping for `explorables.kphutt.com` |
| `explorables-repo.tf` | The explorables repository's own deployer, image repository and WIF provider |
| `outputs.tf` | The values the deploy workflow needs as repository variables |
| `seed.py` | The one-time steps OpenTofu cannot do: the project, the GitHub environment, the repository variables |
| `tests/plan.tftest.hcl` | Offline plan tests with mocked providers |
| `tests/dns.tftest.hcl` | Offline tests that pin every production record's value |
| `tests/test-domain.tftest.hcl` | Offline tests that the test name can never be `kphutt.com` or `www` |
| `tests/site-domain.tftest.hcl` | Offline tests of the live names' ownership record and mappings |
| `tests/explorables.tftest.hcl` | Offline tests of the explorables service, mapping, deploy rights (both repositories'), cutoff and alerts |

## Who changes what

- **OpenTofu owns the infrastructure.** Every change lands through a reviewed pull
  request, and is applied by hand from a dev machine. CI cannot: the deployer
  identity it gets can push an image and deploy a revision of the site's service or the
  explorables service, and nothing else.
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
6. `mise exec -- python infra/seed.py vars --apply` sets the seven repository variables the
   deploy workflow reads. The Cloud Run deploy job skips itself until `WIF_PROVIDER` is set,
   and the workflow still builds and checks the site.

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
test name was enough: Cloud Run accepted the mapping. `site-domain.tf` now verifies
`kphutt.com` as well (see "The live names"). If the test name's mapping then asks for a
CNAME, because it has become a subdomain of a verified name, its check stops the plan; set
`verify_test_hostname = false` and apply again. The test name then gets one CNAME to
`ghs.googlehosted.com` instead.

To switch it off, set `test_domain_enabled = false` and apply: the test name's records, the
mapping and the ownership CNAME are removed. OpenTofu forgets Google's ownership record rather than
deleting it, because Google's API refuses to delete it while the CNAME still exists. Google
re-checks ownership from time to time and drops it once the CNAME is gone.

`kphutt.com` itself is not here. Its records are in `dns.tf`.

## The live names (`site-domain.tf`)

The mappings that serve `kphutt.com` and `www.kphutt.com` from Cloud Run, and the proof
that the account running OpenTofu owns `kphutt.com`. Always on. A mapping serves a name only
while that name's DNS points at Google. The apex and `www` records that point there are in
`dns.tf`, so creating a mapping moves no traffic until its records point at Google.

The apply:

1. asks Google for an ownership token for `kphutt.com` (the DNS_CNAME method) and creates
   the CNAME it names, at `<token>.kphutt.com`. A new name, so it touches no existing
   record. The method that puts a TXT record on the apex is not used: the apex's TXT
   records are mail's (SPF), in `dns.tf`;
2. has Google check it, which makes the account running OpenTofu a verified owner of
   `kphutt.com`;
3. creates the two mappings, and checks each asks for exactly its list: Google's four A and
   four AAAA addresses for the apex, one CNAME to `ghs.googlehosted.com` for `www` (a
   subdomain of a verified name). Anything else stops the apply. `dns.tf` holds the same
   records, and `tests/dns.tftest.hcl` checks that the two match.

### Guards on the ownership record

The ownership record is the DNS record that proves to Google you own kphutt.com. Cloud Run
needed that proof to map kphutt.com and www, and Google re-checks it from time to time.
Four guards protect it and the two mappings:

- A precondition limits its name to one label of letters, digits and hyphens directly under
  kphutt.com, never `www` or the test name `next`.
- A precondition pins the record to the code Google gave the account that set this up: its
  label and target, in `apex_verification_expected`. Another account gets a different code,
  so its plan stops with an error instead of rewriting the record.
- `scripts/check_infra.py` refuses the record outside `site-domain.tf`, and requires both
  preconditions, the Site Verification resource and both mappings there.
- `prevent_destroy` stops OpenTofu from deleting the record or either mapping.

Run OpenTofu as the account that set this up. Change the pin only in a reviewed pull
request, such as one that moves ownership to another account.

## Production DNS (`dns.tf`)

Every record in the kphutt.com zone, except the test name's and kphutt.com's ownership record
(`site-domain.tf`), is in `dns.tf`, as three resources:

- `cloudflare_dns_record.web` (list `local.web_records`): the apex's four A and four AAAA
  records and the `www` CNAME that serve the live site, pointing at the Cloud Run mappings
  in `site-domain.tf`.
- `cloudflare_dns_record.mail` (list `local.mail_records`): seven MX, and the SPF, DKIM and
  DMARC TXT records.
- `cloudflare_dns_record.services` (list `local.service_records`): `lodestar.kphutt.com`, a
  CNAME to `ghs.googlehosted.com` for the lodestar service, which runs in its own Google
  Cloud project. Its domain mapping and certificate are managed there. And
  `explorables.kphutt.com`, the same kind of CNAME, for this project's explorables service
  (`explorables.tf` holds its mapping). In this list, the
  guards below allow only CNAMEs to Google's mapping host, one label under the zone.

All are DNS only. They are separate resources, not one, because `prevent_destroy` covers a whole
resource. So a change that has to delete a web record can unlock the web records alone,
leaving mail and the services locked.

A web record may be written before it exists, with `id = null`: its import is skipped and
the apply creates it. The apex's AAAA records were added that way. Their Cloudflare ids are now
in `dns.tf`, so on a fresh state the import blocks adopt them. Mail records always have an id.

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

- `prevent_destroy` is on every resource, so any plan that would delete or replace a
  production record fails.
  - Services: never lifted, same as mail.
  - Mail: never lifted. `scripts/check_infra.py` requires `prevent_destroy = true` on
    `cloudflare_dns_record.mail` and has no setting that allows anything else. Deleting a
    mail record means changing that rule, in review.
  - Web: lifted only by a reviewed pull request whose change, or its undo, has to delete a web
    record. A later pull request sets it back, as pull request C did after the switch
    (`docs/design/cutover-runbook.md`). The unlock changes exactly two lines, and either line
    alone fails the check:
    - in `dns.tf`, `prevent_destroy = true` becomes `false` on `cloudflare_dns_record.web`.
    - in `scripts/check_infra.py`, `WEB_PREVENT_DESTROY = "true"` becomes `"false"`.
- `scripts/check_infra.py` fails unless `dns.tf` holds only these three resources. Each has
  its own fixed list, with one literal line per record, its own import block, DNS only,
  automatic TTL and `prevent_destroy` as above. Each list allows only its own kind of
  record, so a record cannot be moved between lists to unlock it:
  - web: A, AAAA and CNAME records named `kphutt.com` or `www.kphutt.com`
  - mail: MX and TXT
  - services: CNAMEs to `ghs.googlehosted.com`, one label under the zone

  Only `dns.tf` may import a Cloudflare record, and no other file may use any of the lists.
  The test name and anything under it are refused here, and every other file is still
  refused the apex and `www`.
- Nothing may loosen those pins: `dns.tf` may not use `ignore_changes` or
  `replace_triggered_by`, and no `moved` or `removed` block may be in `dns.tf` or name any
  of them from another file (either would take a record out from under its pins without a
  plan deleting it).
- `tests/dns.tftest.hcl` pins every name, type, value and MX priority.

## Explorables (`explorables.tf`)

A second Cloud Run service, `explorables`, for the pages in `explorables/`, on its own origin
so the site keeps `script-src 'none'` (`AGENTS.md`, "Explorables"). It has the site's shape:
scale to zero, the same instance cap, the same explicit TCP startup probe, the site's empty
runtime account and the site's image repository. The deployer gets `run.developer` on it and
nothing else new, the cost cutoff turns it off with the site, and the uptime alerts watch
its `robots.txt`.

Its mapping asks for one CNAME to `ghs.googlehosted.com`, like `www`, because
`kphutt.com` is already verified. That record is in `dns.tf`'s service list, DNS only.

Like `cloud-run.tf`, `explorables.tf` does not ignore `template[0].revision`, and its deploy
never names a revision (no `--tag` or `--revision-suffix` when it creates one). A name
written into the template would make a later apply of any template change resend it and fail
with a 409. `scripts/check_infra.py` refuses either change.

Turning it on, in this order:

1. Apply: from `infra/`, with `CLOUDFLARE_API_TOKEN` set, `mise exec -- tofu plan -out=site.tfplan`,
   read it, then `mise exec -- tofu apply site.tfplan`. It creates the service on the
   placeholder image, the mapping and the DNS record. Google issues the certificate once the
   record resolves, usually within 15 minutes.
2. `mise exec -- python infra/seed.py vars --apply` sets `EXPLORABLES_SERVICE`. Until then
   the `explorables-deploy` job skips itself, and the site's deploy is unaffected either way.
3. The next merge to `main`, or a manual run of `hugo.yml`, deploys the explorables image.
   After that, `rollback.yml` with service `explorables` rolls back a bad deploy, and with
   service `site` the main site. It fails, changing nothing, while `EXPLORABLES_SERVICE` is
   unset.
4. A pull request writes the new record's Cloudflare id into `dns.tf` in place of `null`.

### The explorables repository (`explorables-repo.tf`)

Explorables are moving to their own repository, `kphutt/explorables`
(`explorables_github_repository`), which deploys the same service with its own identity. That
identity is the narrowest that can deploy it:

- **Its own service account,** `explorables-deployer`, never the site's `site-deployer`. It
  gets `run.developer` on the explorables service only, `artifactregistry.writer` on its own
  image repository only, and `serviceAccountUser` on the empty runtime account the service runs
  as. Nothing on the site's service or image repository, IAM, DNS or the project.
- **Its own image repository,** `explorables`, with the site's cleanup rules, so nothing pushed
  or tagged from that repository can touch the site's images.
- **Its own WIF provider,** `github-explorables`, in the same pool. Its condition admits only
  that repository by numeric id, on `main`, from its `ci.yml` and `rollback.yml`, in the
  `cloud-run` environment, from a push or a manual run. The site's provider and its deployer
  binding are unchanged, and neither provider admits the other repository's id, so neither
  repository can become the other's deployer.

The service keeps running as the site's runtime account rather than one of its own. That
account holds no permissions, and a second one would also need the cost cutoff to act as it.

Until `explorables_github_repository_id` is set, the provider and the binding are not
created, `WIF_PROVIDER` in `tofu output explorables_github_variables` is empty, and that
repository's deploy job skips itself. The site's own explorables deploy keeps working
alongside, until this repository's explorables jobs are removed.

Turning it on, once the repository exists:

1. Add `explorables_github_repository_id = "<id>"` to `terraform.tfvars`, from
   `gh api repos/kphutt/explorables --jq .id`. Plan, read, apply, as in step 1 above.
2. `python infra/seed.py explorables-env --apply` creates that repository's `cloud-run`
   environment, `main` only.
3. `mise exec -- python infra/seed.py explorables-vars --apply` sets its six repository
   variables. Its next merge to `main` deploys.

After its first good deploy, stop this repository deploying explorables: a pull request removes
`explorables/` and its jobs here, `deployer_deploys_explorables` and `EXPLORABLES_SERVICE`.
Until then both repositories can deploy the same service, and a merge to either one's `main`
replaces what the other deployed.

## Alerts (`uptime.tf`)

Two emails go to the alert channel, from uptime checks that fetch `https://<name>/robots.txt`
every 15 minutes from three regions (it must end at 200 after any redirects, with a valid
certificate):

- **Site down**: the check fails from two or more regions for 30 minutes (two checks in a row).
- **TLS certificate expiring**: a certificate has fewer than `uptime_cert_expiry_days` days
  left (default 14). Google renews its managed certificates automatically, so this
  means it has not been renewed yet. Each alert says what to do.

`kphutt.com` and `www.kphutt.com` are watched by default, and `explorables.kphutt.com` by its own
check, which a `terraform.tfvars` setting `uptime_hostnames` cannot drop. Google follows redirects and judges the final answer, so `www`, which
redirects to the apex, is held to the final 200. That still proves its certificate handshake
and that the redirect lands on a live page. Its certificate-expiry alert measures the apex's
certificate. To watch another name, add it to `uptime_hostnames` in the git-ignored `terraform.tfvars`.

Cost is $0: uptime checks get 1,000,000 free executions a month per billing account (this
setup uses about 26,000 with three names), alerts on uptime metrics are not charged, and
`/robots.txt` is tiny ([pricing](https://cloud.google.com/stackdriver/pricing)).

## Cost

Fixed monthly cost is $0. Cloud Run scales to zero and its free tier covers this site's
traffic many times over; images and logs stay inside their free allowances (the image
repository keeps the 20 newest images, and request logs are kept 30 days). The budget alert
emails at 10%, 50% and 100% of `monthly_budget_usd` (default $1), and when the month's
forecast crosses it.
