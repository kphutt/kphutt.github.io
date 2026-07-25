# kphutt.github.io — contributor guide

## What This Is
Personal website for Karsten Huttelmaier. Hugo static site deployed to GitHub Pages.

## Architecture
- Hugo with PaperMod theme
- Homepage: `content/_index.md` (custom layout renders as content page, not post list)
- Posts: `content/posts/` (individual markdown files, rendered as post list)
- Deployment: GitHub Actions on push to main

## Key Decisions
- Single homepage does the heavy lifting; notes section grows by accretion
- PaperMod theme with custom homepage layout override (`layouts/index.html`)
- No JavaScript required. No build tools beyond Hugo.
- Content is markdown. Structure is in the content, not the theme.

## Commands
```bash
hugo server -D    # Local dev server with drafts
hugo              # Build to /public
```

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

### Before switching the DNS proxy on
Proxying puts a reverse proxy in front of Pages. Three things to settle first, or the site breaks:

1. **Set SSL/TLS mode to Full (strict) first.** On Flexible the proxy fetches the origin over
   plain HTTP, Pages redirects that to HTTPS because Enforce HTTPS is on, and the redirect
   repeats forever — visitors get a redirect loop and the site is effectively down. Full (strict)
   is fine because Pages presents a valid certificate for the domain.
2. **Exempt `/.well-known/acme-challenge/*` from any Always Use HTTPS rule.** GitHub renews the
   Pages certificate by serving a file at that path over plain HTTP. A proxy sitting in front of
   it is the usual cause of renewal failures, and a failed renewal only takes HTTPS down when the
   certificate expires — roughly 90 days after the change that caused it, long past the point
   anyone connects the two.
3. **Expect a second cache layer.** Pages already sends `Cache-Control: max-age=600`; the proxy
   adds its own on top, so a deploy may not show up until that cache is purged.

Proxied or not, the built URLs stay correct, because they come from `baseURL` rather than from
whatever is answering at the edge.
