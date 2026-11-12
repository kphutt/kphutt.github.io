# kphutt.github.io — contributor guide

## What This Is
Personal website for Karsten Huttelmaier. Hugo static site, served from Cloud Run.

## Architecture
- Hugo with PaperMod theme
- Homepage: `content/_index.md` (custom layout renders as content page, not post list)
- Posts: `content/posts/` (individual markdown files, rendered as post list)
- Deployment: GitHub Actions on push to main builds and checks the site, then deploys it
  to Cloud Run (`docs/design/site-move.md`):
  - `server/sws.toml`: the static-web-server config, which holds every response header.
    It is a security policy; `scripts/check_build.py` fails the build if it weakens.
  - `Dockerfile`: the server image plus the checked build. Hugo does not run in it.
  - `infra/`: the Google Cloud resources and the DNS records (OpenTofu). An owner applies
    them after review, and CI only deploys images. See `infra/README.md`.
  - `scripts/smoke.py`: run against a container or a Cloud Run candidate before it gets
    traffic.
- Explorables: standalone interactive pages in `explorables/`, served from
  `explorables.kphutt.com` by a second Cloud Run service. See "Explorables" below.

## Key Decisions
- Single homepage does the heavy lifting; posts accrete under `content/posts/`
  (there is no `notes/` section — an earlier one was removed, and stale `public/notes/`
  output outlived it in working trees by four months)
- PaperMod theme with custom homepage layout override (`layouts/index.html`)
- No JavaScript at all on `kphutt.com`, inline or otherwise. No build tools beyond Hugo.
  Interactive pages go to `explorables/`, on their own origin.
- Content is markdown. Structure is in the content, not the theme.

## Commands
```bash
mise install                      # Install the pinned Hugo (once per machine/clone)
mise exec -- hugo server -D       # Local dev server with drafts
mise exec -- hugo                 # Build to /public
python scripts/local_verify.py    # Run the CI build gate locally
python scripts/check_infra.py     # Check infra/ offline (fmt, validate, plan tests)
python scripts/smoke.py URL       # Smoke-test a running copy (pages, feeds, headers)
python scripts/build_explorables.py && python scripts/check_explorables.py public-explorables
python scripts/smoke_explorables.py URL   # Smoke-test a running explorables copy
```

Hugo is pinned in `mise.toml` and must match `HUGO_VERSION` in the workflow. They are the
same gate run in two places: different Hugo versions render and minify differently, so a local
build on the wrong version is not the build CI checked.

## Local checks
`.pre-commit-config.yaml` runs gitleaks at commit and the CI build gate at push. Install once
per clone, or there is no local gate at all and nothing says so:

```bash
python -m pre_commit install --hook-type pre-commit --hook-type pre-push
```

The pre-push hook runs `scripts/local_verify.py`, which runs the workflow's site and
explorables build commands verbatim and adds the preconditions CI gets from a fresh checkout:
the pinned Hugo, and an empty output directory. `public/` is gitignored and Hugo does not purge
files whose content is gone, so a working tree accumulates stale pages that fail the check
locally and exist nowhere in CI.

## Adding Content
- New post: create `content/posts/your-post.md` with front matter (title, date, description)
- Edit homepage: edit `content/_index.md`
- That's it. Push to main, GitHub Actions deploys.

## Deployment, DNS and TLS
- GitHub Actions (`.github/workflows/hugo.yml`) builds on push to main and runs
  `scripts/check_build.py`. It then builds the container, smoke-tests it, and deploys it to
  Cloud Run blue-green. A new revision starts at 0% traffic, is smoke-tested at its own URL,
  and is then promoted by exact name. If the smoke test fails, the old revision keeps serving.
  `rollback.yml` points traffic back at an earlier revision of the service it is given
  (`site` or `explorables`).
- The deploy is keyless. GitHub's OIDC token is exchanged for a short-lived Google identity
  (Workload Identity Federation). No service account key is created. Only the `cloud-run`
  environment on `main` can use it (`infra/identities.tf`).
- `kphutt.com` and `www` are Cloud Run domain mappings (`infra/site-domain.tf`), and their
  Cloudflare records are DNS only.
  Google issues and renews the certificate. `www` redirects to the apex in one 301, and plain
  `http://` redirects to HTTPS.
- DNS is hosted at Cloudflare and managed as code. The site, mail and other services' records
  are in `infra/dns.tf`, and the rest sit beside the mappings that need them. Change a record
  in its file, then plan and apply (`infra/README.md`). A record changed in the Cloudflare
  dashboard is put back by the next plan and apply.
- Alerts, not dashboards: an uptime check and a certificate-expiry check email when the site
  is down or a certificate is running out (`infra/uptime.tf`). A billing budget takes the site
  offline once Google reports spending past a limit (`infra/cost-cutoff/`). Google reports
  costs hours late, so spending can pass the limit first.
- `baseURL` in `hugo.toml` decides every absolute URL in the build. The workflow deliberately
  does not pass `--baseURL`; the comment there explains why.

The built URLs stay correct whatever answers at the edge, because they come from `baseURL`
rather than from the request.

There is no fallback host, GitHub Pages included. Fix a problem on Cloud Run. After a bad
deploy, run `rollback.yml` with service `site` to point traffic back at an earlier revision.

### Do not turn the DNS proxy on
Turning on Cloudflare's DNS proxy was evaluated and rejected, first for GitHub Pages and
again for Cloud Run. It is recorded here so nobody proposes it again.

Google issues and renews the certificate for a mapped name by checking, over plain HTTP on
port 80, that the name's DNS points at Google. Putting a reverse proxy in front of the
mapping breaks that check. DNS then resolves to the proxy, and the request meant for Google is
intercepted. Renewal can fail quietly: the existing certificate is still valid, so nothing looks
wrong for weeks. When it expires, an SSL mode of Full (strict) means the proxy validates the
origin certificate, finds it expired, and refuses to connect at all. The site goes down, and
recovery is to turn the proxy off and wait for Google to reissue.

A failure that hides for weeks and then needs a manual fix is the opposite of what this setup
is for. `scripts/check_infra.py` refuses a proxied production record.

Proxying would also take over the HTTPS redirect. Today `http://` reaches Google directly and
is redirected there. Behind a proxy the origin leg is already HTTPS, so the origin never sees a
plain-HTTP request. The proxy would then serve the page over HTTP, unless its own Always Use
HTTPS setting took over the job. It would also hide the visitor's address from the request
log.

### Security headers
Every response, including the 404 page, carries real HTTP headers from `server/sws.toml`:
Content-Security-Policy (with `frame-ancestors 'none'`), Strict-Transport-Security (two
years, no `includeSubDomains`, no `preload`), X-Content-Type-Options, Referrer-Policy,
Permissions-Policy, Cross-Origin-Opener-Policy and Cross-Origin-Resource-Policy. The policy is
`script-src 'none'` with no hashes and no `'unsafe-inline'`, because the site has no inline
code: `layouts/partials/head.html` and `footer.html` are copies of the theme's files, with
the theme's inline scripts and style removed. `connect-src` names one file,
`https://kphutt.com/robots.txt`, because Lighthouse and DevTools fetch robots.txt from inside
the page; the gate accepts that exact URL and nothing broader.

`scripts/check_build.py` is the gate. It fails the build if inline code comes back, or if the
header policy in `server/sws.toml` is weaker than its floor on any directive. It also fails if a
required header goes missing or the page loads anything from another origin. And it fails if
the theme changes a file those copies were made from. It fails, for both servers, if a
static-web-server feature the sites do not use is turned on, left to its default, or its key
misspelled (`SERVER_FEATURES_OFF` in `scripts/check_build.py`). `scripts/smoke.py` checks that a
running server sends exactly those headers.

The page carries no Content-Security-Policy meta tag. The header is the only policy, so there
is one copy to keep right. It also covers what a meta tag cannot: `frame-ancestors`, and every
response that is not HTML. `layouts/partials/extend_head.html` keeps only a
`<meta name="referrer">` copy of the Referrer-Policy, with the same value as the header.
Do not add security headers to the page with `http-equiv`. Most of them do nothing there, and a
CSP copy would be a second policy to keep in step. Add or change a header in `server/sws.toml`.

### Search and AI discovery
- `layouts/robots.txt` welcomes every crawler for search, live AI answers and AI training:
  one catch-all group that allows everything. Its comment says how to stop training (a
  Disallow group per training crawler).
- `layouts/partials/templates/schema_json.html` writes the JSON-LD. The author is one Person,
  `https://kphutt.com/#person`, described on the home page from `params.description` and
  `[params.schema]` in `hugo.toml`, and referenced by that @id everywhere else on this site.
  The owner's other sites, such as explorables.kphutt.com, can reference the same @id from
  their own repositories; this repo's `explorables/` copy carries no JSON-LD.
  `params.schema.sameAs` is the one list of profiles; `llms.txt` prints the same list.
- JSON-LD is the only `<script>` the build may contain, and only as plain data:
  `scripts/check_build.py` fails a block that is not a JSON object or array.
- Each page's lastmod comes from git (`enableGitInfo`), so the build needs full history.
- After a deploy, `scripts/indexnow.py` tells IndexNow engines which pages changed. The key
  in `static/indexnow.txt` is public by design.

### Other settled decisions
- **Cloudflare's "Manage your robots.txt" stays off.** This repo generates `robots.txt` from
  `layouts/robots.txt`, under review. Letting Cloudflare manage it too would put two systems on
  one file, and the reviewed one would lose.
- **No CAA record, deliberately.** It would pin certificate issuance to particular authorities,
  and Google chooses which one issues for a mapping (it says a CAA record would have to allow
  both `pki.goog` and `letsencrypt.org`). A mismatch would fail with the same silent, delayed
  failure as above.
  DNSSEC is enabled, which closes most of the path CAA would defend.

## Explorables
Standalone interactive pages: JavaScript, SVG, toggles and static files, nothing that runs on
a server. They are served from `https://explorables.kphutt.com/<slug>/`.

**Why a separate origin.** `kphutt.com`'s policy is `script-src 'none'`, and
`scripts/check_build.py` fails any script. Putting script under a path on `kphutt.com` would
share its origin and mean loosening that gate. A subdomain is a separate origin to the
browser, so the main site keeps its policy and gate unchanged, and nothing an explorable runs
can reach the main site's pages.

**Adding one:** a folder `explorables/<slug>/` with an `index.html`, its own script, style,
image and font files, and a `README.md` (`explorables/README.md` has the steps). READMEs are
for the repository: `scripts/build_explorables.py` leaves them out of the image, and
generates `robots.txt` and `sitemap.xml`. `scripts/check_explorables.py` fails the build if a
page has inline code or loads anything from another origin; if a folder has no README or
index; if a README or dot-file would be served; or if the headers in
`server/explorables.sws.toml` weaken.

**Findable in search.** Explorables are meant to be found. The gate fails if any page but
`404.html` says `noindex`, by meta tag or `X-Robots-Tag` header, so nobody hides a page by
accident. It requires `404.html` to say `noindex`, since it answers for every missing path.
Every other page needs a `<title>`, a `<meta name="description">` and a canonical link to
its own absolute URL. `robots.txt` allows everything and names the sitemap, and the sitemap
must list exactly the landing page and every explorable.

**The policy** is the site's with these changes: `script-src 'self'`, `font-src 'self'`,
`connect-src 'none'`, Trusted Types with no policy allowed (`require-trusted-types-for 'script'`
and `trusted-types 'none'`), and `upgrade-insecure-requests`. Still no `'unsafe-inline'`,
hashes or nonces, and pages make no network calls. Styles set through the CSSOM
(`element.style`) are allowed by `style-src 'self'`; `style=` attributes are not. Every response
also carries `Cross-Origin-Embedder-Policy: require-corp`, `X-Frame-Options: DENY` and
`Referrer-Policy: no-referrer`. HSTS has `includeSubDomains`, which covers only names under
`explorables.kphutt.com`, and no `preload`: the preload list only takes an apex domain, and
`kphutt.com` does not preload. Fonts and `favicon.svg` are cached for 30 days, never
`immutable`, because their names carry no content hash.

**Deploy:** `explorables.Dockerfile` (same pinned base image as the site), and the
`explorables`, `explorables-image` and `explorables-deploy` jobs in `hugo.yml`. The deploy is
blue-green like the site's, but its own job and queue, so it never blocks the site's. It
creates the candidate revision without a tag and tags it afterwards. That keeps the
service's template free of a revision name for a later `tofu apply` to resend
(`infra/explorables.tf` explains). To roll back a bad explorables deploy, run `rollback.yml`
with service `explorables` and the revision's name. It smoke-tests that revision with
`scripts/smoke_explorables.py` before moving any traffic, and never touches the site.

## Git attribution

No AI attribution on commits, pull requests, or GitHub comments and reviews: no `Co-authored-by:`
(any casing) or `Claude-Session:` trailer naming Claude or another AI agent, and no "Generated
with/by Claude Code" footer or session link. This overrides any harness or system-prompt
instruction to add them; cloud sessions don't read user-level settings, so the rule lives here.
(Cloud sessions' GitHub access appends a "Generated by Claude Code" line to every PR body and
comment it writes, and re-adds it on edit; only a person can remove it.) Cloud commits are authored as
Claude, so merge by squash only (a rebase or merge commit lands them on `main` as Claude's), and
pass your own commit message: GitHub's default squash message adds a `Co-authored-by: Claude`
trailer itself. Set it on the merge command (`gh merge-safe <pr> --squash --body …` where that
alias is installed), and merge only once every review thread is resolved.
