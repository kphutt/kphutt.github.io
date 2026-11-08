# Cutover runbook: kphutt.com from GitHub Pages to Cloud Run

Steps 4 and 5 of [the plan](site-move.md). Every DNS change here is made by hand in the
Cloudflare dashboard, by hand. Each step says how to undo it.

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

These four A records and the CNAME are GitHub Pages. **Putting exactly these back is the
rollback**, at any point. Do not touch the MX and TXT records: they are for mail.

## Step 4: prove it on `next.kphutt.com`

Prerequisite: the infrastructure is applied and one deploy has run, so the service's
`*.run.app` address serves the site (`python scripts/smoke.py <service_url>` passes).

1. Verify ownership of `kphutt.com` with Google, once: `gcloud domains verify kphutt.com`
   opens Search Console, which asks for a TXT record. Add it in Cloudflare. It does not
   affect the site.
2. Map the test name, option A in [the ADR](../decisions/0001-custom-domain-on-cloud-run.md):
   ```bash
   gcloud beta run domain-mappings create --service site --domain next.kphutt.com --project "$PROJECT" --region "$REGION"
   ```
   Add the record it prints (a CNAME to `ghs.googlehosted.com`), DNS only. Wait for the
   certificate (usually about 15 minutes, up to a day):
   `gcloud beta run domain-mappings describe --domain next.kphutt.com --project "$PROJECT" --region "$REGION"`.
   Undo: delete the CNAME and
   `gcloud beta run domain-mappings delete --domain next.kphutt.com --project "$PROJECT" --region "$REGION"`.
3. Measure, and write the results into the ADR:
   - `python scripts/smoke.py https://next.kphutt.com`
   - `curl -sI` on `/`, `/posts/brochure/`, the stylesheet and a missing page
   - which TLS versions are offered: `openssl s_client -connect next.kphutt.com:443 -servername next.kphutt.com -tls1` (then `-tls1_1`, `-tls1_2`, `-tls1_3`)
   - Mozilla HTTP Observatory, and SSL Labs, on `next.kphutt.com`
   - a headless Chromium load of a page: zero CSP violations, zero requests to any other
     host
   - one request with a known referrer and user agent, then find it in the
     `site-requests` log bucket and note the client address it recorded
4. If option A offers TLS 1.0 or 1.1, try option D on the same name before choosing (the
   ADR describes it). Record the same measurements.
5. Decide, and set the ADR's status to Accepted. Then follow the step 5 that matches the
   choice. Choosing anything other than A or D (the load balancer, say) means writing its
   own step 5 here first.

## Step 5 under option A: the apex, by domain mapping

Only after step 4 is recorded and the decision is made.

1. Map both names:
   ```bash
   gcloud beta run domain-mappings create --service site --domain kphutt.com --project "$PROJECT" --region "$REGION"
   gcloud beta run domain-mappings create --service site --domain www.kphutt.com --project "$PROJECT" --region "$REGION"
   ```
   Note the A and AAAA records the apex mapping prints.
2. In Cloudflare, replace the four GitHub A records with the mapping's A and AAAA records,
   and point `www` at `ghs.googlehosted.com`. DNS only (see the ADR on why not a flattened
   CNAME).
   **Rollback:** restore the table above exactly, and delete the AAAA records. GitHub Pages
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
4. Retire Pages, as below.

## Step 5 under option D: the apex, through a Cloudflare Worker

Only after step 4 is recorded and the decision is made. D needs no domain mapping for the
apex; delete the `next.kphutt.com` mapping from step 4 if it is no longer used.

1. Deploy the Worker that forwards to the service's `*.run.app` address (the same script
   step 4 tested on `next.kphutt.com`), and add the route `kphutt.com/*` to it.
2. In Cloudflare, under SSL/TLS: Minimum TLS Version 1.2, Always Use HTTPS on. Under Web
   Analytics: automatic injection off for this site.
3. Replace the four GitHub A records with one proxied placeholder record for the apex
   (`AAAA 100::`, see the ADR). For `www`: replace its CNAME with a proxied placeholder, and
   add a Single Redirect rule sending `www.kphutt.com/*` to `https://kphutt.com/${1}` with
   a 301, path and query kept.
   **Rollback:** restore the table above exactly, which also turns the proxy off (the
   original records are DNS only), and delete the redirect rule. The Worker route then
   matches nothing that reaches Cloudflare, so it can stay until it is cleaned up. The
   same certificate caveat as option A applies to the speed of this rollback.
4. Watch, exactly as in option A step 3, including the `www` redirect.
5. Retire Pages, as below.

## Retiring Pages (either option)

Once the new site has been stable:

1. As its own pull request:
   - remove the `deploy` job and the Pages artifact upload from `hugo.yml`;
   - remove the `CNAME` check from `scripts/check_build.py` and delete `static/CNAME`;
   - delete the in-page CSP meta tag in `layouts/partials/extend_head.html` (the header
     replaces it), and drop `check_page_policy` from `scripts/check_build.py`, which then
     checks the header policy in `server/sws.toml` alone;
   - update the "Deployment, DNS and TLS" section of `AGENTS.md`.
2. Then, outside the repo: disable GitHub Pages in the repository settings and delete the
   `github-pages` environment. The required status check is `build`, which stays: it is the
   summary job that passes only when the site build, the container smoke test and the infra
   checks all pass. Branch protection needs no change.
