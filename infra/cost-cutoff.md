# The cost cutoff

The site's `*.run.app` address is public, so anyone can send it traffic, and traffic costs
money. The $1 budget in `monitoring.tf` only sends email. The cost cutoff acts: when the
month's **actual** spend reaches `cost_cutoff_usd` (default $10), it turns the site off.

The resources are in `cost-cutoff.tf`. The function is `cost-cutoff/main.py`.

## How it works

1. A second budget, "Site cost cutoff", watches the project. Its amount is the cutoff.
2. Cloud Billing sends that budget's current numbers to a Pub/Sub topic several times a day.
3. Each message starts the function. It reads `costAmount`, the month's actual cost so far.
   If that is at or above the cutoff, it sets the site's Cloud Run **ingress** to
   **internal only**.
4. The same budget emails the existing alert address when actual spend crosses the
   cutoff, so you hear about it at the moment it trips. The function also writes an
   `ALERT` log line that starts with `COST CUTOFF TRIPPED`.

It never acts on a forecast. Billing data runs a few hours behind, so the real stop point
is a little above the cutoff. Pick the cutoff with that in mind.

## Why it turns ingress off, and not something else

Google's guide offers two patterns: detach billing from the project, or stop chosen
resources. Detaching billing stops everything, and Google warns that resources "might be
irretrievably deleted". That is too much for a site whose fix is a single setting.

Setting ingress to internal only:

- **stops the cost.** Requests from the internet are refused at Google's front end, for the
  `run.app` address and any custom domain. They never reach an instance, so a flood costs
  nothing more.
- **deletes nothing.** The service, its revisions, its images, its IAM and its logs all
  stay exactly as they were.
- **is one field.** The function changes only `ingress` (an `updateMask` request), so no
  new revision is made and nothing else moves.
- **is one command to undo** (below).

Other options, and why not:

- **Remove the public `allUsers` invoker.** It would also stop the site, but that grant is a
  resource in `cloud-run.tf`, so the next `tofu apply` would put it straight back. It also
  needs permission to change the service's IAM policy, which is a bigger power to hand out.
- **Lower max instances.** That limit lives in the revision template, which every deploy
  and every `tofu apply` writes, so the next one would quietly undo it. And it caps the
  cost of a flood rather than stopping it.

## What the function may do

It runs as its own service account, `site-cost-cutoff`, which holds:

- a custom role with three permissions, `run.services.get`, `run.services.update` and
  `run.operations.get`, bound **on the site service only**. It cannot delete the service,
  change its IAM or touch any other service. No project-wide role.
- `iam.serviceAccountUser` on the site's runtime account. Cloud Run asks for this whenever
  a service is updated. The runtime account holds no permissions, so acting as it reaches
  nothing.
- `run.invoker` on the function itself, so Eventarc can deliver messages to it.

The function only accepts internal traffic (Eventarc in this project). Nobody on the
internet can call it.

A second account, `site-cost-cutoff-build`, is what Cloud Build uses to package the
function. It can write build logs, push to the function's own image repository, and read
the function's source buckets. A condition on that last grant keeps it out of every other
bucket, including the OpenTofu state.

## Reopening the site after a trip

**First**, find out why it tripped (the request logs in the `site-requests` log bucket
show who was sending traffic).

**Then**, if this month's spend is still above the cutoff, the next budget message will
turn the site straight back off. So, if you are reopening in the same month, first raise
`cost_cutoff_usd` in `terraform.tfvars` and apply:

```bash
cd infra
mise exec -- tofu plan -out=site.tfplan   # shows the budget and the function changing
mise exec -- tofu apply site.tfplan
```

**Finally**, reopen the site. This is the one command (`tofu output
cost_cutoff_reopen_command` prints it with your values filled in):

```bash
gcloud run services update site --region=us-west1 --project=<project_id> --ingress=all
```

A new month starts the count again from zero, but the site stays off until you run that
command. It never reopens itself.

## What does not reopen it

- **`tofu apply`.** `cloud-run.tf` ignores the service's ingress, so applying any change
  leaves a tripped site off. The cost of that: OpenTofu no longer notices if ingress is
  changed by hand. The plan tests still check the value it is created with, and a unit test
  (`cost-cutoff/test_main.py`) fails if the ignore is removed.
- **A deploy.** The deploy workflow never sets ingress. While the site is off, its smoke
  test cannot reach the candidate revision from GitHub, so the deploy fails before moving
  any traffic. That failure is expected until you reopen.

## Setup, once

The APIs the cutoff needs (Pub/Sub, Cloud Functions, Cloud Build, Eventarc, Cloud Storage)
are in `seed.py`. On a project seeded before they were added, run this again first. It is
safe to re-run:

```bash
python infra/seed.py project --apply
```

Then plan and apply as usual. Creating the budget gives Cloud Billing permission to publish
to the topic; the account running `tofu apply` needs `pubsub.topics.setIamPolicy` for that,
which a project owner has. To confirm it worked:

```bash
gcloud pubsub topics get-iam-policy site-cost-cutoff --project=<project_id>
```

It should list a Cloud Billing account with `roles/pubsub.publisher`. The first budget
message can take a few hours to arrive; after that, the function's logs show
`Spend is under the cutoff; nothing to do.` several times a day.

## Testing it without spending money

Publish a fake budget message to the topic (it is the same shape Cloud Billing sends). With
`costAmount` under the cutoff, the function logs that and does nothing:

```bash
gcloud pubsub topics publish site-cost-cutoff --project=<project_id> \
  --message='{"costAmount": 0.01, "budgetAmount": 10, "currencyCode": "USD"}'
```

A `costAmount` at or above the cutoff really turns the site off, so only send one when you
mean to, and reopen it afterwards with the command above.

## Cost

Nothing fixed. The budget and topic are free; the function runs a few thousand times a
month, well inside the free invocations, and scales to zero. The source bucket holds one
small zip in a free-tier region. The function's image repository keeps only its newest
build; that image is a few hundred megabytes, which shares Artifact Registry's free 0.5 GB
with the site's images. If the two ever pass it together, the overage costs cents a month.

## Tests

```bash
python -m unittest discover -s infra/cost-cutoff   # the function, Cloud Run API mocked
python scripts/check_infra.py                      # includes tests/cost-cutoff.tftest.hcl
```
