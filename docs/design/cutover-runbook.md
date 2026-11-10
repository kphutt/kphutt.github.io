# Cutover runbook: kphutt.com from GitHub Pages to Cloud Run

Steps 4 and 5 of [the plan](site-move.md). Each step says how to undo it.

Step 4 is code: OpenTofu creates the test name's mapping and DNS record
(`infra/test-domain.tf`), and that file cannot touch `kphutt.com` or `www`, by design. The
zone's production records, the apex, `www` and mail, are code too, in `infra/dns.tf` and
nowhere else. So every step 5 DNS change below is: change the record in `infra/dns.tf`, plan,
apply (`infra/README.md`, "Production DNS"). Never change them in the Cloudflare dashboard:
the next plan would put them back.

Every `gcloud` command names its project and region explicitly. Set them once in the shell
first, so a different default project can never receive a mapping:

```bash
PROJECT=<project_id>   # from infra/terraform.tfvars
REGION=<region>
```

## The records as they are today

Read from public DNS on 2026-11-08. All TTLs are already 300 seconds, so there is nothing
to lower in advance: a change, or its undo, reaches resolvers within five minutes.

| Name | Type | Value | Proxy |
|---|---|---|---|
| `kphutt.com` | A | `185.199.108.153` | DNS only |
| `kphutt.com` | A | `185.199.109.153` | DNS only |
| `kphutt.com` | A | `185.199.110.153` | DNS only |
| `kphutt.com` | A | `185.199.111.153` | DNS only |
| `www.kphutt.com` | CNAME | `kphutt.github.io` | DNS only |

These four A records and the CNAME are GitHub Pages, and they are `local.web_records` in
`infra/dns.tf` (resource `cloudflare_dns_record.web`). **Putting exactly these back is the
rollback**, at any point: revert the change to `infra/dns.tf`, plan, apply. Do not touch the
MX and TXT records (`local.mail_records`): they are for mail, and their `prevent_destroy`
never comes off. (Cloudflare stores these TTLs as 1, "automatic", which it serves as 300.)

### Unlocking the web records

Replacing a record deletes the old one, and `prevent_destroy` on `cloudflare_dns_record.web`
refuses that. So the cutover pull request below also changes exactly these two lines, and
nothing else about the guards:

- `infra/dns.tf`, in `resource "cloudflare_dns_record" "web"`: `prevent_destroy = true`
  becomes `prevent_destroy = false`.
- `scripts/check_infra.py`: `WEB_PREVENT_DESTROY = "true"` becomes `"false"`.

The check fails if only one of them changes, and it refuses `prevent_destroy = false` on
`cloudflare_dns_record.mail` whatever `WEB_PREVENT_DESTROY` says. They stay unlocked through
the watch, so a rollback is a plain revert of the record lines; then a pull request sets both
lines back to `true` (option A step 4, option D step 5). A rollback after that carries the
same two-line unlock.

## Step 4: prove it on `next.kphutt.com`

Prerequisite: the infrastructure is applied and one deploy has run, so the service's
`*.run.app` address serves the site (`python scripts/smoke.py <service_url>` passes).

1. Do the one-time setup in `infra/README.md`, "The test name": the Cloudflare token, the
   zone id, and signing in with the Site Verification scope.
2. Map the test name, option A in [the ADR](../decisions/0001-custom-domain-on-cloud-run.md):
   set `test_domain_enabled = true` in `infra/terraform.tfvars`, then from `infra/`, with
   `CLOUDFLARE_API_TOKEN` set:
   ```bash
   mise exec -- tofu plan -out=site.tfplan
   mise exec -- tofu apply site.tfplan
   ```
   Read the plan first: it must add only records named `next.kphutt.com` and
   `<token>.next.kphutt.com`, the ownership record and the mapping, and change nothing
   else. The apply proves ownership of the test name, creates the mapping and adds the four
   A and four AAAA records it asks for, DNS only (`infra/README.md` explains why a verified
   name gets these rather than a CNAME). Wait for the certificate (usually about 15 minutes,
   up to a day):
   `gcloud beta run domain-mappings describe --domain next.kphutt.com --project "$PROJECT" --region "$REGION"`.
   Undo: set `test_domain_enabled = false`, plan and apply. That removes the test name's
   records, the ownership CNAME and the mapping.
3. Measure, and write the results into the ADR:
   - `python scripts/smoke.py https://next.kphutt.com`
   - `curl -sI` on `/`, `/posts/brochure/`, the stylesheet and a missing page
   - which TLS versions are offered: a Python TLS probe, one handshake per version (the
     ADR records its result). `openssl s_client -connect next.kphutt.com:443 -servername next.kphutt.com -tls1`
     (then `-tls1_1`, `-tls1_2`, `-tls1_3`) gives the same answer
   - Mozilla HTTP Observatory on `next.kphutt.com`, and an SSL Labs scan with its results
     not published (both recorded in the ADR)
   - a headless Chromium load of a page: zero CSP violations, zero requests to any other
     host
   - one request with a known referrer and user agent, then find it in the
     `site-requests` log bucket and note the client address it recorded
4. Option A offers TLS 1.0 and 1.1; the owner accepted that for now (the ADR has the
   measurements). Option D stays untried unless the TLS question is reopened.
5. Decided: option A, ADR status Accepted. Follow "Step 5 under option A". The option D
   section below is kept in case the decision changes; choosing anything other than A or D
   (the load balancer, say) means writing its own step 5 here first.

## Step 5 under option A: the apex, by domain mapping

Only after step 4 is recorded and the decision is made.

1. Map both names:
   ```bash
   gcloud beta run domain-mappings create --service site --domain kphutt.com --project "$PROJECT" --region "$REGION"
   gcloud beta run domain-mappings create --service site --domain www.kphutt.com --project "$PROJECT" --region "$REGION"
   ```
   Note the A and AAAA records the apex mapping prints.
2. Change the record in `infra/dns.tf`, plan, apply: replace the four GitHub A records with
   the mapping's A and AAAA records, and point `www` at `ghs.googlehosted.com`. DNS only (see
   the ADR on why not a flattened CNAME). Update `tests/dns.tftest.hcl` to match in the same
   pull request, and unlock the web records in it ("Unlocking the web records" above).
   Read the plan: it must touch only `cloudflare_dns_record.web`, never
   `cloudflare_dns_record.mail`.
   **Rollback:** restore the table above exactly, and delete the AAAA records: revert that
   change, plan, apply. GitHub Pages
   is still configured and still holds a valid certificate for `kphutt.com`, so it serves
   again as soon as resolvers see the old records (five minutes). That certificate cannot
   renew while DNS points elsewhere, so this fast rollback only lasts as long as it does
   (check its expiry before cutting over); after that, a rollback also waits for GitHub to
   reissue it.
3. Watch: `python scripts/smoke.py https://kphutt.com` once the certificate is active, and
   then again over a few days. Check `www` still redirects to the apex, path included, as it
   does on Pages: `curl -sI https://www.kphutt.com/posts/brochure/` must answer 301 with
   `Location: https://kphutt.com/posts/brochure/` (the server does this by host name,
   `server/sws.toml`). Keep Pages configured during this time, so the rollback stays instant.
4. Once the watch is over, lock the web records again: a pull request that sets both lines
   from "Unlocking the web records" back to `true`. Its plan changes nothing.
5. Retire Pages, as below.

## Step 5 under option D: the apex, through a Cloudflare Worker

Only after step 4 is recorded and the decision is made. D needs no domain mapping for the
apex; delete the `next.kphutt.com` mapping from step 4 if it is no longer used.

1. Deploy the Worker that forwards to the service's `*.run.app` address (the same script
   step 4 tested on `next.kphutt.com`), and add the route `kphutt.com/*` to it.
2. In Cloudflare, under SSL/TLS: Minimum TLS Version 1.2, Always Use HTTPS on. Under Web
   Analytics: automatic injection off for this site.
3. Change the records in `infra/dns.tf`, plan, apply: replace the four GitHub A records
   with one proxied placeholder record for the apex (`AAAA 100::`, see the ADR).
   Unlock the web records in the same pull request ("Unlocking the web records" above).
   `scripts/check_infra.py` refuses a proxied production record, so its `proxied` rule
   changes for `cloudflare_dns_record.web` only, in the same review; mail stays DNS only.
   For `www`: replace its CNAME with a proxied placeholder, and
   add a Single Redirect rule sending `www.kphutt.com/*` to `https://kphutt.com/${1}` with
   a 301, path and query kept.
   **Rollback:** restore the table above exactly, which also turns the proxy off (the
   original records are DNS only), and delete the redirect rule. The Worker route then
   matches nothing that reaches Cloudflare, so it can stay until it is cleaned up. The
   same certificate caveat as option A applies to the speed of this rollback.
4. Watch, exactly as in option A step 3, including the `www` redirect.
5. Lock the web records again, as in option A step 4.
6. Retire Pages, as below.

## Retiring Pages (either option)

Once the new site has been stable, and not before the TLS question in the ADR has been
revisited. Revisit by 2027-02-10 (three months), before GitHub Pages is switched off: Pages
stays on until that review is done.

1. As its own pull request:
   - remove the `deploy` job and the Pages artifact upload from `hugo.yml`;
   - remove the `CNAME` check from `scripts/check_build.py` and delete `static/CNAME`;
   - delete the in-page CSP meta tag in `layouts/partials/extend_head.html` (the header
     replaces it), and drop the in-page policy check (`check_page_policy`, added with the
     inline-code removal) from `scripts/check_build.py`, which then
     checks the header policy in `server/sws.toml` alone;
   - update the "Deployment, DNS and TLS" section of `AGENTS.md`.
2. Then, outside the repo: disable GitHub Pages in the repository settings and delete the
   `github-pages` environment. The required status check is `build`, which stays: it is the
   summary job that passes only when the site build, the container smoke test and the infra
   checks all pass. Branch protection needs no change.
