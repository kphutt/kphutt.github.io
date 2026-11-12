# Cutover runbook: how kphutt.com moved from GitHub Pages to Cloud Run

Status: done. The switch was applied at 04:40 UTC on 2026-11-11, and Cloud Run serves
`kphutt.com` and `www`. The steps below are the record of how. There is no fallback host,
so problems are fixed on Cloud Run. After a bad deploy, run `rollback.yml` to point traffic
back at an earlier revision.

Steps 4 and 5 of [the plan](site-move.md).

Step 4 is code: OpenTofu creates the test name's mapping and DNS record
(`infra/test-domain.tf`), and that file cannot touch `kphutt.com` or `www`, by design. Step 5
is code too: the ownership proof for `kphutt.com` and the mappings for the apex and `www` are
in `infra/site-domain.tf`, and the zone's production records, the apex, `www` and mail, are in
`infra/dns.tf` and nowhere else. So every step 5 DNS change below is: change the record in
`infra/dns.tf`, plan, apply (`infra/README.md`, "Production DNS"). Never change them in the
Cloudflare dashboard: the next plan would put them back.

Every `gcloud` command names its project and region explicitly. Set them once in the shell
first, so a different default project can never receive a mapping:

```bash
PROJECT=<project_id>   # from infra/site.tfvars
REGION=<region>
```

## The GitHub Pages records before the switch

These were the records before the switch, read from public DNS on 2026-11-08. All TTLs were
300 seconds, so a resolver's cached copy of a record expired within five minutes.
(Cloudflare stores these TTLs as 1, "automatic", which it serves as 300.)

| Name | Type | Value | Proxy |
|---|---|---|---|
| `kphutt.com` | A | `185.199.108.153` | DNS only |
| `kphutt.com` | A | `185.199.109.153` | DNS only |
| `kphutt.com` | A | `185.199.110.153` | DNS only |
| `kphutt.com` | A | `185.199.111.153` | DNS only |
| `www.kphutt.com` | CNAME | `kphutt.github.io` | DNS only |

These four A records and the CNAME pointed at GitHub Pages. The same records, with the same
Cloudflare ids, are now `local.web_records` in `infra/dns.tf` (resource
`cloudflare_dns_record.web`), holding Google's addresses. The switch left the MX and TXT
records (`local.mail_records`) alone. They are for mail, and their `prevent_destroy` never
comes off.

### Unlocking the web records

`prevent_destroy` on `cloudflare_dns_record.web` refuses any plan that deletes a web record.
So a pull request whose change, or its undo, has to delete one also changes exactly these two
lines, and nothing else about the guards:

- `infra/dns.tf`, in `resource "cloudflare_dns_record" "web"`: `prevent_destroy = true`
  becomes `prevent_destroy = false`.
- `scripts/check_infra.py`: `WEB_PREVENT_DESTROY = "true"` becomes `"false"`.

The check fails if only one of them changes, and it refuses `prevent_destroy = false` on
`cloudflare_dns_record.mail` whatever `WEB_PREVENT_DESTROY` says. Pull request C (below) set
both lines back to `true`.

## Step 4: prove it on `next.kphutt.com`

Prerequisite: the infrastructure is applied and one deploy has run, so the service's
`*.run.app` address serves the site (`python scripts/smoke.py <service_url>` passes).

1. Do the one-time setup in `infra/README.md`, "The test name": the Cloudflare token, the
   zone id, and signing in with the Site Verification scope.
2. Map the test name, option A in [the ADR](../decisions/0001-custom-domain-on-cloud-run.md):
   set `test_domain_enabled = true` in `infra/site.tfvars`, then from `infra/`, with
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

Only after step 4 is recorded and the decision is made. Three pull requests (A, B, C), applied by
hand in order, each from `infra/` with `CLOUDFLARE_API_TOKEN` set and the Site Verification
scope signed in (`infra/README.md`, "The test name", step 3). Each plan is saved and read
before it is applied:

```bash
mise exec -- tofu plan -out=site.tfplan
mise exec -- tofu apply site.tfplan
```

### The certificate gap: read this before choosing a time

Google issues the certificate for a mapped name only after that name's DNS points at
Google. Its check reaches the name over plain HTTP on port 80, so nothing done in advance
can make the certificate exist before the switch: there is no way to pre-issue it for a
domain mapping. (A load balancer can do that, with a DNS authorization, but that is
option C and costs about $18 a month.) Creating the mappings first, in pull request A, only
takes the mapping setup off the clock.

So from the moment resolvers see the new records until the certificate is issued,
`https://kphutt.com` and `https://www.kphutt.com` fail with a certificate error for every
visitor sent to Google. How long:

- Google's page: "usually about 15 minutes but can take up to 24 hours".
- Measured on `next.kphutt.com`, from the mapping's own status: the mapping and its records
  were created at 20:21 UTC on 2026-11-09, and the certificate was ready at 21:18. That is
  56 minutes.

Expect about an hour, and plan for longer. Switch at a quiet time, and stay at the
keyboard until the certificate is ready.

Measured on `kphutt.com` itself, the switch was applied at 04:40 UTC on 2026-11-11. Both
names answered over HTTPS with a valid certificate by 04:59, 19 minutes later.

Nothing in the zone blocks issuance: there is no CAA record (Google's page says one would
have to allow `pki.goog` and `letsencrypt.org`), the records are DNS only, so port 80
reaches Google, and nothing proxies the validation request.

Visitors whose resolvers still held the old records kept reaching GitHub Pages for up to
five minutes (TTL 300). Pages served them correctly, because `static/CNAME` still named
`kphutt.com` and Pages still held its certificate for `kphutt.com` and `www`.

### Pull request A: prove ownership and create the mappings (no traffic moves)

`infra/site-domain.tf`: a CNAME at `<token>.kphutt.com` that proves ownership of
`kphutt.com` to Google, the ownership record itself, and the mappings for `kphutt.com` and
`www.kphutt.com`. It also lets `infra/dns.tf` hold a web record with `id = null` (a record
the next apply creates), which pull request B needs for the apex's new AAAA records.

1. Merge, then plan. **Expected plan:** `4 to add, 0 to change, 0 to destroy`. The four are
   `cloudflare_dns_record.apex_verification` (a CNAME named `<token>.kphutt.com`,
   pointing at `gv-….dv.googlehosted.com`),
   `google_site_verification_web_resource.apex`, and
   `google_cloud_run_domain_mapping.apex` and `.www`. Nothing in `cloudflare_dns_record.web`,
   `.mail` or `.test` may appear.
2. Apply. Each mapping checks that Google asks for exactly its list in `site-domain.tf`:
   the eight A and AAAA addresses for the apex, one CNAME to `ghs.googlehosted.com` for
   `www`. If Google asks for anything else, the apply stops at that mapping with a message
   saying so. Read what it asked for, and do not go on to B until the list is right:
   ```bash
   gcloud beta run domain-mappings describe --domain kphutt.com --project "$PROJECT" --region "$REGION"
   gcloud beta run domain-mappings describe --domain www.kphutt.com --project "$PROJECT" --region "$REGION"
   ```
   Both show `DomainRoutable` true and `CertificateProvisioned` not yet true. That is
   expected: the certificate waits for B.
3. Check the test name is unchanged: plan again and expect `No changes`. Its mapping was made
   when only `next.kphutt.com` was verified. If Google now asks it for a CNAME instead (it
   is a subdomain of a verified name now), the mapping's own check stops the plan; then set
   `verify_test_hostname = false` in `infra/site.tfvars`, or switch the test name off.

**Undo:** the mappings and the ownership record carry `prevent_destroy`, so removing them is
a pull request that lifts it and deletes them. Until B is applied, nothing points at them.

Apply B soon after A, the same day if possible. A mapping whose DNS points elsewhere keeps
failing its certificate check, and Google does not say how its retries slow down.

### Pull request B: the switch (traffic moves)

Opened once A is merged and applied (it is built on A). Two commits, both in
`infra/dns.tf`, `scripts/check_infra.py` and `infra/tests/dns.tftest.hcl`:

1. The unlock: the two lines in "Unlocking the web records" above.
2. The switch, in `local.web_records`:
   - the four apex A records keep their keys and Cloudflare ids, and their addresses become
     Google's four (`216.239.{32,34,36,38}.21`). Same record, new value, so Cloudflare
     changes each in place;
   - four new AAAA records for the apex (`2001:4860:4802:{32,34,36,38}::15`), with
     `id = null`: they do not exist yet, so nothing imports them, and the apply creates
     them;
   - `www` keeps its key and id, and its CNAME points at `ghs.googlehosted.com`, in place;
   - `tests/dns.tftest.hcl` to match, plus a test that the apex records are exactly what
     the apex mapping asks for and `www` is exactly what the `www` mapping asks for.

Mail does not change. In this provider (Cloudflare 5.28) a record's type change forces a
replacement but a content change does not, so this plan deletes nothing.

1. Before merging: describe both mappings once more (A, step 2). Pick a quiet hour.
2. Merge, then plan. **Expected plan:** `4 to add, 5 to change, 0 to destroy`, all in
   `cloudflare_dns_record.web`. To add: `web["apex-aaaa-32"]` to `web["apex-aaaa-38"]`. To
   change, in place: `web["apex-a-108"]` to `web["apex-a-111"]` (content
   `185.199.x.153` to `216.239.x.21`) and `web["www"]` (`kphutt.github.io` to
   `ghs.googlehosted.com`). Any replacement, anything in `cloudflare_dns_record.mail` or
   `.test`, or any Google resource: stop.
3. Apply. Note the time.
4. Watch the certificates every few minutes by requesting both names.
   `curl -sI https://kphutt.com/` must answer 200, and `curl -sI https://www.kphutt.com/`
   must answer 301, both with no certificate error. The mapping status helps explain a
   delay, but it does not say when the certificate is ready:
   ```bash
   gcloud beta run domain-mappings describe --domain kphutt.com --project "$PROJECT" --region "$REGION" --format="value(status.conditions)"
   gcloud beta run domain-mappings describe --domain www.kphutt.com --project "$PROJECT" --region "$REGION" --format="value(status.conditions)"
   ```
   The status lags behind. On 2026-11-11 both names served a valid certificate 19 minutes
   after the apply, while both mappings still said `CertificateProvisioned` Unknown. That
   condition turned true 37 minutes after the apply. A watch that waited on the status alone
   could have undone a switch that already worked.
   The limit was two hours. Had either name not been served by then, the plan was to put
   back the Pages records and find out why.
5. Run the checks in "After the switch" below.

### After the switch

Once both certificates are ready:

1. `python scripts/smoke.py https://kphutt.com`: every page in the sitemap answers 200,
   `robots.txt`, `llms.txt`, `sitemap.xml`, the RSS feed and the og-image answer with
   their content types, the missing-page 404 is the site's own page, and every response
   carries the headers in `server/sws.toml`.
2. `www` redirects to the apex with the path and query kept, in one 301:
   `curl -sI "https://www.kphutt.com/posts/brochure/?x=1"` answers `301` with
   `Location: https://kphutt.com/posts/brochure/?x=1`.
3. Plain HTTP redirects to HTTPS: `curl -sI http://kphutt.com/posts/brochure/` and
   `curl -sI http://www.kphutt.com/` answer with a redirect to `https://`.
4. The link preview: paste `https://kphutt.com/` into a social site's post preview (for
   example LinkedIn's Post Inspector) and check the og-image shows. The image is sent with
   `Cross-Origin-Resource-Policy: same-origin`; that only stops browsers embedding it on
   other sites, and preview services fetch it from their servers, so it should show. If it
   does not, that header is the first suspect.
5. The request log: make one request with a made-up user agent, and find it in the
   `site-requests` log bucket, as in step 4.
6. SSL Labs on `kphutt.com` (results not published): expect B, for TLS 1.0 and 1.1, as on
   the test name. Anything worse: compare with the ADR.
7. Mozilla HTTP Observatory on `kphutt.com`: expect A+.
8. Over the next few days, run `python scripts/smoke.py https://kphutt.com` again.

Result on 2026-11-11: checks 1 to 3 passed, and both names served Google Trust Services
certificates valid until 2027-02-09. Plain HTTP answers 302 rather than 301. That still
sends visitors to HTTPS, and after the first visit HSTS has the browser go there directly.

### Pull request C: lock the web records again

After the watch. In `infra/dns.tf`, write the Cloudflare ids of the four AAAA records into
their lines, so a fresh state adopts them rather than creating copies (read each from state:
`mise exec -- tofu state show 'cloudflare_dns_record.web["apex-aaaa-32"]'`, the `id` line),
and set both lines from "Unlocking the web records" back to `true`. **Expected plan:**
`No changes`.

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
4. Watch, exactly as in option A, "After the switch", including the `www` redirect.
5. Lock the web records again, as in option A, pull request C.
6. Retire Pages, as below.

## Retiring Pages (either option)

After the switch was verified, GitHub Pages was retired in its own pull request. Pages is
not kept as a fallback ([the ADR](../decisions/0001-custom-domain-on-cloud-run.md), "To
revisit"). That pull request:

- removed the `deploy` job and the Pages artifact upload from `hugo.yml`.
- removed the `CNAME` check from `scripts/check_build.py` and deleted `static/CNAME`.
- deleted the in-page CSP meta tag in `layouts/partials/extend_head.html`, because the header
  replaces it. It also dropped `check_page_policy` from `scripts/check_build.py`, which now
  checks the header policy in `server/sws.toml` alone.
- rewrote the "Deployment, DNS and TLS" and "Security headers" sections of `AGENTS.md`.
- rewrote the other docs that still described Pages as live, or the switch as still to come.

Two steps outside the repo follow its merge. Turn Pages off in the repository settings
(Settings, Pages: remove the custom domain, then unpublish the site). Then delete the
`github-pages` environment (Settings, Environments).

Branch protection needs no change. Its required status check is `build`, the summary job
that passes only when the site build, the container smoke test and the infra checks all
pass.
