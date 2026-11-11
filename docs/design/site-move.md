# Moving the site from GitHub Pages to Cloud Run

Status: the move is done, and the site is served from Cloud Run. GitHub Pages is still on in
the repository settings until its owner turns it off. The text below is the plan, with notes
where it has since been done.

## Why

GitHub Pages cannot send HTTP response headers. Most of the security headers a site should
send (HSTS, `X-Content-Type-Options`, CSP `frame-ancestors`, `Permissions-Policy`) only work
as real headers, so on Pages they are simply absent. Moving the same static files to a small
container on Google Cloud Run lets the site send them, while staying static, public,
indexable and free to run.

## What does not change

- The site is still Hugo + PaperMod, built from `main`, and fully static. Nothing runs per
  request except a file server.
- Every URL stays the same: pages, `index.xml` feeds, `sitemap.xml`, `robots.txt`,
  `llms.txt`, canonical tags, schema.org data and the og-image.
- The page loads nothing from any other domain, and runs no JavaScript at all. It uses
  no web fonts either: the theme's system font stack renders with fonts already on the
  reader's device, so there is nothing to self-host. If a font is ever added, it is
  self-hosted as a subset `.woff2` with its licence file.
- Fixed monthly cost is $0. Cloud Run scales to zero; a billing budget alert is the backstop.

## Target shape

```
GitHub Actions (on merge to main, keyless via Workload Identity Federation)
  hugo --gc --minify  ->  scripts/check_build.py  ->  container image
    static-web-server (pinned by digest) + server/sws.toml + public/
  -> Artifact Registry -> Cloud Run candidate revision at 0% traffic
  -> smoke test the candidate URL (status codes + every required header)
  -> promote by exact revision name, or leave the old revision serving
```

- **Server:** static-web-server v2 (the stable line). Its config file sets every header, so
  the policy lives in the repo next to the code it describes: `server/sws.toml`.
- **Infra:** a flat OpenTofu folder `infra/` (no modules), state in a versioned GCS bucket,
  in its own GCP project so nothing can cross with other services. It holds Artifact
  Registry, the Cloud Run service (min instances 0), the deployer service account, the
  Workload Identity pool bound to this repository's `main` branch only, a billing budget
  with alerts, and a log exclusion.
- **Deploy:** on merge to `main`, like the Pages workflow today. A static site rebuilt from
  reviewed `main` does not need a human button. Blue-green: candidate at 0%, smoke, promote.
  A separate rollback workflow points traffic back at a named revision. The deploy workflow
  also accepts `workflow_dispatch`, which is the hook a future scheduled rebuild will use.
- **Domain:** a Cloud Run domain mapping, DNS only (option A), proven on a test subdomain
  before the apex moves. It offers TLS 1.0 and 1.1, which is accepted for now. See
  [the domain ADR](../decisions/0001-custom-domain-on-cloud-run.md).

## Headers

Set in `server/sws.toml` for every response, including the 404 page:

| Header | Value |
|---|---|
| `Content-Security-Policy` | `default-src 'none'; script-src 'none'; style-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'` |
| `Strict-Transport-Security` | `max-age=63072000` (no `includeSubDomains`, no `preload`: both are hard to undo, and other subdomains will exist) |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | every powerful feature off |
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Cross-Origin-Resource-Policy` | `same-origin` |
| `Cache-Control` | one year + `immutable` for the fingerprinted stylesheet; five minutes for HTML, feeds and text; one day for other static files |

The CSP starts from `default-src 'none'` and allows only what the built site loads: its own
stylesheet and its own images. Today's in-page policy is the floor, and this one is stricter
on every directive. There is no `'unsafe-inline'` and there are no hashes, because there is
no inline code left to allow.

`scripts/check_build.py` stays the gate, and gains a check against the header policy: the
built site may contain no inline script, no inline style, no event-handler attributes, and
nothing the policy would block. It also refuses a policy weaker than the floor, so
loosening the policy fails the build.

## Order of work

Each step is its own pull request, through the normal gate.

1. **Hugo 0.169 and licences.** Bump both Hugo pins together. Add Apache-2.0 for the code
   and CC BY 4.0 for `content/`, with a README section and a footer line. (This plan and the
   domain ADR land first, on their own.)
2. **Remove every inline script and style.** Still on Pages, so the in-page CSP keeps
   working and shrinks to `script-src 'none'`. Theme files this needs to override are pinned,
   so a theme update that changes them fails the build until someone re-syncs the copy.
3. **Infra, container and deploy workflow.** Serves the `*.run.app` URL only. The deploy job
   does nothing until the project exists and the repository variables are set, so merging
   it cannot break the Pages deploy. The required status check `build` becomes a summary
   job that passes only when the site build, the container smoke test and the infra checks
   all pass.
4. **Headers verified on the test subdomain** (`next.kphutt.com`): `curl -I` on a page, a
   post, an asset and a 404; a TLS scan; a headless Chromium run showing zero CSP violations
   and zero requests to other domains; an Observatory grade.
5. **Apex cutover, then retire Pages** ([runbook](cutover-runbook.md), with the exact
   records to restore). The records' TTL is already five minutes, so a change and its undo
   both take effect quickly. Pages kept serving until the new site was verified. Then it
   was retired: the Pages deploy job and `static/CNAME` were removed, the in-page CSP was
   dropped, and `AGENTS.md` was updated. The required check stays `build`, so branch
   protection did not change.

## Steps an owner runs

Everything is set up from code, OpenTofu or `infra/seed.py`, even one-time steps. What a
person does is run it with an owner's credentials, and the few things no code can do:

- Run `infra/seed.py` (project, billing, APIs, state bucket; the `cloud-run` GitHub
  environment; the repository variables) and the first `tofu apply`, signed in as an
  owner. The apply creates the identity CI deploys with.
- Sign in, and create the Cloudflare API token for the test name. The token stays in the
  password manager and reaches OpenTofu only through the environment.
- The test subdomain's DNS record, its domain mapping and the proof of ownership for it
  are OpenTofu (`infra/test-domain.tf`), and cannot touch `kphutt.com` or `www`.
- The zone's production records, `kphutt.com`, `www` and mail, are OpenTofu
  (`infra/dns.tf`), adopted as they were. The DNS changes at the cutover are edits to that
  file, made in review, then a plan and an apply.
- Turn off Cloudflare Web Analytics automatic injection for the zone, if it is on.
- After the cutover: remove the Pages workflow, then turn Pages off in the repository
  settings and delete the `github-pages` environment (the runbook, "Retiring Pages").

## Analytics

None on the page. Cloud Run request logs (status, path, referrer, user agent) are the
record. A log exclusion keeps them from being stored twice if a log sink is added later.

## Open questions

- Whether to close the TLS 1.0 and 1.1 gap. The ADR says to revisit it by 2027-02-10 (three months).
  Certificate renewal on the mapping is still unproven: it needs about 90 days.
