# `infra/`: the site's Google Cloud resources (OpenTofu)

One flat folder, no modules. State lives in a versioned GCS bucket in the same project.
The plan this implements is [docs/design/site-move.md](../docs/design/site-move.md).

| File | What it holds |
|---|---|
| `versions.tf` | OpenTofu and provider pins; the partial GCS backend |
| `variables.tf` | Inputs; account-specific ones have no default |
| `site.tfvars` | The values every plan uses. Committed; nothing secret or personal |
| `cloud-run.tf` | The Cloud Run service (min instances 0) and public access |
| `artifact-registry.tf` | The image repository, with a cleanup policy |
| `identities.tf` | The runtime and deployer service accounts, and Workload Identity Federation |
| `monitoring.tf` | The billing budget alert, and the request-log bucket, sink and exclusion |
| `dns.tf` | The zone's production DNS records at Cloudflare: the apex, `www`, mail, services and the Bluesky handle. The only file that may manage them |
| `test-domain.tf` | The test name: domain mapping, its DNS record at Cloudflare, ownership proof. Off by default |
| `site-domain.tf` | The live names: ownership proof for `kphutt.com`, and the mappings for `kphutt.com` and `www` |
| `explorables.tf` | The explorables service and its mapping for `explorables.kphutt.com` |
| `explorables-repo.tf` | The explorables repository's own deployer, image repository and WIF provider |
| `outputs.tf` | The values the deploy workflow needs as repository variables |
| `../infra-apply.toml` | Settings for `scripts/apply_infra.py`: backend, 1Password references, steps |
| `seed.py` | The one-time steps OpenTofu cannot do: the project, the GitHub environment, the repository variables |
| `tests/plan.tftest.hcl` | Offline plan tests with mocked providers |
| `tests/dns.tftest.hcl` | Offline tests that pin every production record's value |
| `tests/test-domain.tftest.hcl` | Offline tests that the test name can never be `kphutt.com` or `www` |
| `tests/site-domain.tftest.hcl` | Offline tests of the live names' ownership record and mappings |
| `tests/explorables.tftest.hcl` | Offline tests of the explorables service, mapping, deploy rights (both repositories'), cutoff and alerts |

## Who changes what

- **OpenTofu owns the infrastructure.** Every change lands through a reviewed pull
  request, and is applied from a dev machine with `scripts/apply_infra.py` (see
  "Applying"). CI cannot: the deployer
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

## Applying

One command plans, checks and applies, from the repository root on any machine:

```bash
python scripts/apply_infra.py --plan-only   # plan and check, apply nothing
python scripts/apply_infra.py               # plan, check, apply, then plan again
```

Nothing has to be set up by hand first. The settings are committed: `infra/site.tfvars`
holds every value a plan needs that is neither secret nor personal, and `infra-apply.toml`
names the state backend and the 1Password references for the rest (the Cloudflare token,
`alert_email` and `billing_account`). The script reads all of them in one `op inject` call,
so a run asks 1Password once. A variable already set in the environment is used instead.
Secrets reach only OpenTofu's environment, and are never printed or written to a file. The
plan file goes in a temporary folder that is deleted when the run ends.

The script refuses a plan, and applies nothing, if it would delete, replace or forget a
resource the command line does not name:

```bash
python scripts/apply_infra.py --allow-replace google_storage_bucket_object.cost_cutoff_source
python scripts/apply_infra.py --allow-delete cloudflare_dns_record.test   # every instance of it
```

An address without an index names every instance of that resource, as `-target` does. An
allowance that matches nothing in the plan also stops the run, because it means the plan is
not the one you expected. Creates and in-place updates are applied without asking, so read
the plan it prints (`--plan-only` first, when in doubt). `prevent_destroy` and the
preconditions still apply underneath.

After the apply it plans again and says whether the infrastructure now matches the code.
Optional steps after the apply:

- `--run seed-vars`: `seed.py vars --apply`, after an apply that changed an output.
- `--run seed-explorables-env` and `--run seed-explorables-vars`: the same two hand-offs for
  the explorables repository (`explorables-repo.tf`): its `cloud-run` environment, `main`
  only, and its repository variables.
- `--deploy`: start `hugo.yml` on `main`, so a service created on the placeholder image gets
  the real one.
- `--check-live MINUTES`: wait for every domain mapping in the project to be Ready and serve
  a valid certificate, then run `scripts/smoke.py` against the live site names
  (`[live.smoke]` in `infra-apply.toml`). `explorables.kphutt.com` is waited for but
  smoke-tested by its own repository's deploy. Needs `gcloud`.

Exit codes: 0 done and clean, 1 usage or setup error, 2 a command failed, 3 the plan was refused, 4 applied but the
second plan still shows changes, 5 the live check failed.

The 1Password items it reads (`infra-apply.toml`, `[[secret]]`) are made once: the Cloudflare
token as in "The test name", step 1, and an item `kphutt-site tofu` with the fields
`alert_email` and `billing_account`. A local `infra/terraform.tfvars` is no longer needed.
OpenTofu still loads one if it exists, so the script warns about it: delete it.

`scripts/apply_infra.py` is the same file in every repository that uses it; everything that
differs between them is in `infra-apply.toml`. Fix it in one place and copy it.

## First-time setup (once)

Every step is a command from this repository. The only things done outside it are signing
in and creating the Cloudflare token.

1. Check the values in `infra/site.tfvars`, and create the 1Password items `infra-apply.toml`
   names (see "Applying"). For another account or project, change both in a pull request.
2. Sign in with an account that can create projects and use the billing account:
   `gcloud auth login` and `gcloud auth application-default login`.
3. `python infra/seed.py project` prints what it will do. Read it, then run it again with
   `--apply`, with `TF_VAR_billing_account` set from its 1Password reference. It creates the project, links billing, enables the APIs and creates the state
   bucket.
4. `python scripts/apply_infra.py` (see "Applying"). This creates the service on Google's placeholder image, so `*.run.app` answers with a
   hello page until the first deploy.
5. `python infra/seed.py env --apply` creates the `cloud-run` environment, or updates it,
   so that only `main` can deploy to it. It needs `gh` signed in as a repository admin.
   (The first deploy would create the environment anyway, without that limit; the Google
   side refuses anything but `main` either way.)
6. `python scripts/apply_infra.py --run seed-vars` sets the seven repository variables the
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
   variable `CLOUDFLARE_API_TOKEN`; `scripts/apply_infra.py` reads it from the reference in
   `infra-apply.toml`. It never goes in a `.tfvars` file or the repository.
2. The zone id, from the kphutt.com Overview page in Cloudflare, is `cloudflare_zone_id` in
   `infra/site.tfvars`. It is not a secret.
3. Proving ownership uses Google's Site Verification API, which needs its own OAuth scope.
   Sign in for OpenTofu once more, with that scope added:
   ```bash
   gcloud auth application-default login --scopes=openid,https://www.googleapis.com/auth/userinfo.email,https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/siteverification
   ```
   If the project was seeded before this API was added to `seed.py`, run
   `python infra/seed.py project --apply` again: it is safe to re-run and enables it.

Then set `test_domain_enabled = true` in `infra/site.tfvars` in a pull request. Once it is
merged, run `python scripts/apply_infra.py --check-live 60`. It only creates, so it needs no
allowance. The apply:

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

To switch it off, set `test_domain_enabled = false` in `infra/site.tfvars` in a pull
request. Once it is merged, allow exactly the test name's deletes:

```bash
python scripts/apply_infra.py --allow-delete cloudflare_dns_record.test --allow-delete google_cloud_run_domain_mapping.test --allow-delete cloudflare_dns_record.verification --allow-delete google_site_verification_web_resource.test
```

With `verify_test_hostname = false` there is no ownership record or proof to delete, so leave
out the last two; otherwise the run stops on an allowance that matches nothing. The test
name's records, the mapping and the ownership CNAME are removed. OpenTofu forgets Google's ownership record rather than
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
(`site-domain.tf`), is in `dns.tf`, as four resources:

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
- `cloudflare_dns_record.identity` (list `local.identity_records`): the Bluesky handle record,
  `_atproto.kphutt.com`, a TXT record whose value is `did=` and the DID of the account that
  uses `kphutt.com` as its handle. Bluesky reads it to check the handle. Changing the DID
  changes which account the handle verifies to. Its entry starts with `id = null`, so the
  apply creates it, and the pull request after that apply writes in the id Cloudflare gave it.

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
3. After review and merge, `python scripts/apply_infra.py --plan-only`. Read it: it must
   change only the records you meant to change.
4. `python scripts/apply_infra.py`. A changed value is an update. A change that deletes a
   record also needs `--allow-delete` with its address, after the unlock below.

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
- `scripts/check_infra.py` fails unless `dns.tf` holds only these four resources. Each has
  its own fixed list, with one literal line per record, its own import block, DNS only,
  automatic TTL and `prevent_destroy` as above. Each list allows only its own kind of
  record, so a record cannot be moved between lists to unlock it:
  - web: A, AAAA and CNAME records named `kphutt.com` or `www.kphutt.com`
  - mail: MX and TXT
  - services: CNAMEs to `ghs.googlehosted.com`, one label under the zone
  - identity: exactly one TXT record, with the exact name `_atproto.kphutt.com` and the exact
    DID value. No other underscore name and no wildcard name is allowed in any list.

  Only `dns.tf` may import a Cloudflare record, and no other file may use any of the lists.
  The test name and anything under it are refused here, and every other file is still
  refused the apex and `www`.
- Nothing may loosen those pins: `dns.tf` may not use `ignore_changes` or
  `replace_triggered_by`, and no `moved` or `removed` block may be in `dns.tf` or name any
  of them from another file (either would take a record out from under its pins without a
  plan deleting it).
- `tests/dns.tftest.hcl` pins every name, type, value and MX priority.

## Explorables (`explorables.tf`, `explorables-repo.tf`)

A second Cloud Run service, `explorables`, serves `explorables.kphutt.com`, on its own origin
so the site keeps `script-src 'none'` (`AGENTS.md`, "Explorables"). Its pages, gate,
container, deploy and rollback are in their own repository, `kphutt/explorables`. Its
infrastructure is here, in the same state as the zone. The service has the site's shape:
scale to zero, the same instance cap, the same explicit TCP startup probe and the site's
empty runtime account. The cost cutoff turns it off with the site, and the uptime alerts
watch its `robots.txt`.

Its mapping asks for one CNAME to `ghs.googlehosted.com`, like `www`, because
`kphutt.com` is already verified. That record is in `dns.tf`'s service list, DNS only.

Like `cloud-run.tf`, `explorables.tf` does not ignore `template[0].revision`, and the
explorables deploy never names a revision (no `--tag` or `--revision-suffix` when it creates
one). A name written into the template would make a later apply of any template change
resend it and fail with a 409.

### Its deploy identity (`explorables-repo.tf`)

`kphutt/explorables` (`explorables_github_repository`, by id in `site.tfvars`) deploys the
service with an identity of its own, the narrowest that can:

- **Its own service account,** `explorables-deployer`, never the site's `site-deployer`. It
  gets `run.developer` on the explorables service only, `artifactregistry.writer` on its own
  image repository only, and `serviceAccountUser` on the empty runtime account the service runs
  as. Nothing on the site's service or image repository, IAM, DNS or the project.
- **Its own image repository,** `explorables`, with the site's cleanup rules, so nothing pushed
  or tagged from that repository can touch the site's images.
- **Its own WIF provider,** `github-explorables`, in the same pool. Its condition admits only
  that repository by numeric id, on `main`, from its `ci.yml` and `rollback.yml`, in the
  `cloud-run` environment, from a push or a manual run. Neither provider admits the other
  repository's id, so neither repository can become the other's deployer.

This repository's deployer has no rights on the explorables service.

The service keeps running as the site's runtime account rather than one of its own. That
account holds no permissions, and a second one would also need the cost cutoff to act as it.

If `explorables_github_repository_id` were emptied, the provider and the binding would go,
`WIF_PROVIDER` in `tofu output explorables_github_variables` would be empty, and that
repository's deploy job would skip itself.

To set that repository up again (a new clone of the settings, say):
`python scripts/apply_infra.py --run seed-explorables-env --run seed-explorables-vars`
creates its `cloud-run` environment, `main` only, and sets its six repository variables.

## Alerts (`uptime.tf`)

Two emails go to the alert channel, from uptime checks that fetch `https://<name>/robots.txt`
every 15 minutes from three regions (it must end at 200 after any redirects, with a valid
certificate):

- **Site down**: the check fails from two or more regions for 30 minutes (two checks in a row).
- **TLS certificate expiring**: a certificate has fewer than `uptime_cert_expiry_days` days
  left (default 14). Google renews its managed certificates automatically, so this
  means it has not been renewed yet. Each alert says what to do.

`kphutt.com` and `www.kphutt.com` are watched by default, and `explorables.kphutt.com` by its own
check, which a setting of `uptime_hostnames` cannot drop. Google follows redirects and judges the final answer, so `www`, which
redirects to the apex, is held to the final 200. That still proves its certificate handshake
and that the redirect lands on a live page. Its certificate-expiry alert measures the apex's
certificate. To watch another name, add it to `uptime_hostnames` in `infra/site.tfvars`.

Cost is $0: uptime checks get 1,000,000 free executions a month per billing account (this
setup uses about 26,000 with three names), alerts on uptime metrics are not charged, and
`/robots.txt` is tiny ([pricing](https://cloud.google.com/stackdriver/pricing)).

## Cost

Fixed monthly cost is $0. Cloud Run scales to zero and its free tier covers this site's
traffic many times over; images and logs stay inside their free allowances (the image
repository keeps the 20 newest images, and request logs are kept 30 days). The budget alert
emails at 10%, 50% and 100% of `monthly_budget_usd` (default $1), and when the month's
forecast crosses it.
