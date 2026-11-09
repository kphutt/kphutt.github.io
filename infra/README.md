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
| `test-domain.tf` | The test name: domain mapping, its DNS record at Cloudflare, ownership proof. Off by default |
| `outputs.tf` | The values the deploy workflow needs as repository variables |
| `seed.py` | The one-time steps OpenTofu cannot do: the project, the GitHub environment, the repository variables |
| `tests/plan.tftest.hcl` | Offline plan tests with mocked providers |
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

1. Copy `terraform.tfvars.example` to `terraform.tfvars` (git-ignored) and fill it in.
2. Sign in with an account that can create projects and use the billing account:
   `gcloud auth login` and `gcloud auth application-default login`.
3. `python infra/seed.py project` prints what it will do. Read it, then run it again with
   `--apply`. It creates the project, links billing, enables the APIs and creates the state
   bucket.
4. From `infra/`:
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
at Cloudflare, DNS only. It is off until `test_domain_enabled = true`, so the steps above
need no Cloudflare token.

It manages new records under the test name and nothing else. It does not import or change
any existing record, and `kphutt.com` and `www.kphutt.com` cannot be managed from here:
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
   variable `CLOUDFLARE_API_TOKEN` (for example with `op run`). It never goes in
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
   under the test name (`<token>.next.kphutt.com`). A TXT record cannot be used: it could
   not share a name with the test name's own CNAME;
2. has Google check it, which makes the account running OpenTofu a verified owner of the
   test name (not of `kphutt.com`);
3. creates the domain mapping;
4. creates the CNAME from the test name to the target the mapping gives
   (`ghs.googlehosted.com`). Google then issues the certificate, usually within 15
   minutes and up to a day.

Google's mapping page says to verify the parent domain, `kphutt.com`. Verifying only the
test name is the narrower proof and is reported to work, but it is not the documented path.
If the mapping step refuses the name as unverified, set `verify_test_hostname = false`,
verify `kphutt.com` once with `gcloud domains verify kphutt.com` (Search Console asks for a
TXT record on `kphutt.com`, added by hand: that record is on the apex, so this code will not
create it), and apply again.

To switch it off, set `test_domain_enabled = false` and apply: the record, the mapping and
the ownership CNAME are removed. OpenTofu forgets Google's ownership record rather than
deleting it, because Google's API refuses to delete it while the CNAME still exists. Google
re-checks ownership from time to time and drops it once the CNAME is gone.

`kphutt.com` itself is not here. It moves only after the ADR's decision, and bringing its
existing records under OpenTofu is a separate change.

## Cost

Fixed monthly cost is $0. Cloud Run scales to zero and its free tier covers this site's
traffic many times over; images and logs stay inside their free allowances (the image
repository keeps the 20 newest images, and request logs are kept 30 days). The budget alert
emails at 10%, 50% and 100% of `monthly_budget_usd` (default $1), and when the month's
forecast crosses it.
