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
| `outputs.tf` | The values the deploy workflow needs as repository variables |
| `seed.py` | The one-time steps OpenTofu cannot do, and setting the repository variables |
| `tests/plan.tftest.hcl` | Offline plan tests with mocked providers |

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

## First-time setup (by hand, once)

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
5. Create the `cloud-run` environment in the repository settings, with deployment
   branches limited to `main`. (The first run would create it anyway, without that
   limit; the Google side refuses anything but `main` either way.)
6. `mise exec -- python infra/seed.py vars --apply` sets the six repository variables the
   deploy workflow reads. Until they exist, the Cloud Run deploy job skips itself, so the
   workflow keeps working for GitHub Pages.

The custom domain is not here yet. How `kphutt.com` reaches the service is still being
decided: see [the ADR](../docs/decisions/0001-custom-domain-on-cloud-run.md).

## Cost

Fixed monthly cost is $0. Cloud Run scales to zero and its free tier covers this site's
traffic many times over; images and logs stay inside their free allowances (the image
repository keeps the 20 newest images, and request logs are kept 30 days). The budget alert
emails at 10%, 50% and 100% of `monthly_budget_usd` (default $1), and when the month's
forecast crosses it.
