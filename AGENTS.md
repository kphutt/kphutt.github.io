# kphutt.github.io — contributor guide

## What This Is
Personal website for Karsten Huttelmaier. Hugo static site deployed to GitHub Pages.

## Architecture
- Hugo with PaperMod theme
- Homepage: `content/_index.md` (custom layout renders as content page, not post list)
- Posts: `content/posts/` (individual markdown files, rendered as post list)
- Deployment: GitHub Actions on push to main

## Key Decisions
- Single homepage does the heavy lifting; posts accrete under `content/posts/`
  (there is no `notes/` section — an earlier one was removed, and stale `public/notes/`
  output outlived it in working trees by four months)
- PaperMod theme with custom homepage layout override (`layouts/index.html`)
- No JavaScript required. No build tools beyond Hugo.
- Content is markdown. Structure is in the content, not the theme.

## Commands
```bash
mise install                      # Install the pinned Hugo (once per machine/clone)
mise exec -- hugo server -D       # Local dev server with drafts
mise exec -- hugo                 # Build to /public
python scripts/local_verify.py    # Run the CI build gate locally
```

Hugo is pinned in `mise.toml` and must match `HUGO_VERSION` in the workflow. This is not
housekeeping: `scripts/check_build.py` compares hashes of the theme's inline scripts against
the CSP, hashes pin exact bytes, and Hugo versions minify differently — so the wrong Hugo
fails the check on every page while CI is green.

## Local checks
`.pre-commit-config.yaml` runs gitleaks at commit and the CI build gate at push. Install once
per clone, or there is no local gate at all and nothing says so:

```bash
python -m pre_commit install --hook-type pre-commit --hook-type pre-push
```

The pre-push hook runs `scripts/local_verify.py`, which runs the workflow's two build commands
verbatim and adds the preconditions CI gets from a fresh checkout: the pinned Hugo, and an
empty output directory. `public/` is gitignored and Hugo does not purge files whose content is
gone, so a working tree accumulates stale pages that fail the check locally and exist nowhere
in CI.

## Adding Content
- New post: create `content/posts/your-post.md` with front matter (title, date, description)
- Edit homepage: edit `content/_index.md`
- That's it. Push to main, GitHub Actions deploys.

## Deployment, DNS and TLS
- GitHub Actions builds on push to main (`.github/workflows/hugo.yml`); GitHub Pages serves it.
- The custom domain comes from `static/CNAME`, which Hugo copies into the build output.
  Delete that file and the domain goes away.
- DNS is hosted at Cloudflare, but the site is not proxied: the apex A records point at
  GitHub Pages' addresses and `www` is a CNAME to the Pages host. Names resolve through
  Cloudflare, traffic does not — responses come from GitHub.
- Pages has Enforce HTTPS on. Both `http://` and the `www` host redirect to the apex over HTTPS.
- `baseURL` in `hugo.toml` decides every absolute URL in the build. The workflow deliberately
  does not pass `--baseURL`; the comment there explains why.

Proxied or not, the built URLs stay correct, because they come from `baseURL` rather than from
whatever is answering at the edge.

### Do not turn the DNS proxy on
This was evaluated and rejected. The record is here so it does not get re-proposed.

Putting a reverse proxy in front of Pages breaks GitHub's certificate renewal. DNS then resolves
to the proxy rather than to GitHub, and the ACME challenge GitHub serves over plain HTTP on port
80 gets intercepted. Renewal fails quietly — the existing certificate is still valid, so nothing
looks wrong for weeks. When it expires, roughly 90 days later, an SSL mode of Full (strict) means
the proxy validates the origin certificate, finds it expired, and refuses to connect at all. The
site goes down, and recovery is to un-proxy manually and wait for GitHub to reissue.

A 90-day fuse with a manual recovery step is the opposite of what this setup is for.

Proxying would also silently remove the HTTPS redirect. Today `http://` reaches GitHub directly
and Pages redirects it. Behind a proxy the origin leg is already HTTPS, so Pages never sees a
plain-HTTP request and never redirects; the proxy would serve the page over HTTP unless its own
Always Use HTTPS were enabled to take over the job.

### Security headers: what is possible here, and what is not
GitHub Pages cannot send HTTP response headers at all. This is a platform limitation with no
configuration behind it, and the workarounds all amount to putting something else in front of
the site — see above for why that is not happening.

Two headers have genuine in-document equivalents and are set in
`layouts/partials/extend_head.html`:
- **Content-Security-Policy**, via `http-equiv`, pinning inline scripts by hash.
- **Referrer-Policy**, via `<meta name="referrer">`.

These cannot be set and are deliberately absent rather than faked: **HSTS**,
**X-Content-Type-Options**, **X-Frame-Options**, **Permissions-Policy**, and CSP's
`frame-ancestors` and `report-uri`. Setting any of them with `http-equiv` does nothing except
make the source look secure. Do not add them.

### Other settled decisions
- **Cloudflare's "Manage your robots.txt" stays off.** This repo generates `robots.txt` from
  `layouts/robots.txt`, under review. Letting Cloudflare manage it too would put two systems on
  one file, and the reviewed one would lose.
- **No CAA record, deliberately.** It would pin certificate issuance to one authority, which
  couples the domain to GitHub's choice of CA with the same silent, delayed failure as above.
  DNSSEC is enabled, which closes most of the path CAA would defend.
