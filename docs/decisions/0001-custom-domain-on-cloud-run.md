# 0001. Serving kphutt.com from Cloud Run

- **Status:** Accepted for now (option A). The TLS trade-off is revisited, see Decision.
- **Date:** 2026-11-08. Decided and measured 2026-11-09.

## Context

The site is moving to Cloud Run so it can send real security headers
([plan](../design/site-move.md)). Cloud Run gives every service a `*.run.app` address. The
question is how `kphutt.com` reaches it, under five constraints:

1. $0 fixed monthly cost.
2. No silent, delayed failure. Certificate renewal must not depend on something that only
   breaks 90 days later. `AGENTS.md` records that exact failure as the reason the DNS proxy
   was rejected for Pages.
3. No regression from today. (This decision waives it for TLS versions, see Decision.) Measured on 2026-11-08, the live site on Pages refuses TLS 1.0
   and 1.1 and offers TLS 1.2 and 1.3. The `*.run.app` address of an existing Cloud Run
   service behaves the same.
4. DNS stays at Cloudflare (Free plan), DNSSEC on, and other subdomains will exist later.
5. Everything is set up from code (OpenTofu or a checked-in script), never a console change that can't be repeated.

## Options

### A. Cloud Run domain mapping, DNS-only

Free. Google issues and renews the certificate.

- Still Preview. Google's page calls it "not production-ready", warns of high latency from
  some locations, and recommends a load balancer instead. us-west1 and us-central1 are both
  supported regions.
- **It cannot disable TLS 1.0 and 1.1.** Google's mapping page says so directly, and names
  two ways to get TLS 1.2-only traffic: Cloud Load Balancing (option C) and Firebase Hosting
  (option E). That is a regression from today (constraint 3).
  Scanners such as SSL Labs cap the grade when 1.0 or 1.1 is offered.
- The apex takes the A and AAAA records the mapping shows. Ownership must be verified with
  Google once (a DNS TXT record). For the test, `infra/` proves ownership of the test name
  alone, from code, with a CNAME under it (`infra/README.md`).
- Tried on `next.kphutt.com`: ownership was proven for the test name only, and Cloud Run
  accepted the mapping. Because the mapped name is the verified name, the mapping asked for
  A and AAAA records, the same set an apex gets, not a CNAME.
- Renewal is Google's job end to end, and nothing sits in front to intercept it.

### B. Cloudflare proxy in front of a domain mapping

Free, and Cloudflare's Minimum TLS Version setting (on Free) gives visitors a TLS 1.2 floor.

- Host-header override is Enterprise-only, so the proxied name must be `kphutt.com` itself,
  pointing at the mapping. The reported workaround is to map while DNS-only, then turn the
  proxy on with SSL mode Full (strict).
- **Renewal is the same 90-day fuse `AGENTS.md` describes.** Google's mapping page warns
  that a CDN can intercept certificate validation and break mapping or renewal. Community
  reports show validation requests reaching the mapping over plain HTTP and being
  redirected, after which a certificate expired and would not reissue until the proxy was
  removed. With Full (strict), an expired origin certificate takes the site down.
- The "Always Use HTTPS" conflict: Google says to turn it off so validation can get
  through. With it off, Cloud Run's own front end issues the HTTP to HTTPS redirect, which is
  also what reportedly redirected the validation requests. Neither setting is known to be
  safe.
- The renewal question cannot be settled inside one session: a renewal happens roughly
  every 90 days, and the docs do not say which challenge type is used. This option can only
  be argued from the docs, and the docs argue against it.
- The proxy only sets the floor for visitors who go through it. Google's front end still
  answers TLS 1.0 for anyone who connects to the mapping's addresses directly.

### C. Google external Application Load Balancer + serverless NEG

Fully supported, with an SSL policy that can require TLS 1.2. About $18 a month for one
forwarding rule, which breaks constraint 1. Not pursued unless the free options fail.

### D. Cloudflare proxy + a Worker that forwards to `*.run.app`

The proxied `kphutt.com` record exists only so Cloudflare answers for the name. A small
Worker on the route `kphutt.com/*` fetches the same path from the service's `*.run.app`
address and returns the response unchanged, headers included.

- No domain mapping at all, so no Preview feature and no Google certificate to renew.
  Cloudflare issues and renews the visitor-facing certificate itself, and it handles its own
  validation for proxied names.
- Visitors get Cloudflare's TLS 1.2 floor. The Worker-to-origin leg is `*.run.app`, which
  refuses 1.0 and 1.1.
- "Always Use HTTPS" can simply stay on: there is no Google validation request to protect.
- Costs: free up to 100,000 Worker requests a day, which is far above this site's traffic.
  Past the cap, requests fail rather than fall through. It adds one small edge script and a
  second place configuration lives, plus a Cloudflare API token if it is managed as code.
- Cloud Run's request log sees the Worker, not the visitor. The Worker can pass the
  visitor's address in `X-Forwarded-For`, and static-web-server can log it, but Cloud Run's
  own request log will not show it.
- The `*.run.app` address stays public. Anyone using it gets the same files and the same
  headers over TLS 1.2+, so it bypasses nothing that matters.

### E. Firebase Hosting in front of the service, DNS-only

Google's mapping page names this as one of the two routes to TLS 1.2-only traffic, and
Firebase Hosting can rewrite every path to a Cloud Run service in us-west1 or us-central1.
It has no fixed monthly fee and needs nothing in front of it, so it would not reopen the DNS
proxy question. Its cost is a second set of tools (its own command-line tool, config file and console) plus a CDN cache that can add or change response headers, so it would need the same header check as everything else. It is recorded here because the source used for option A points at it. It was considered and set aside for now.

Limits in Firebase's own docs:

- Only the `__session` cookie is passed through to the backend; other cookies are stripped
  ([cache and cookies](https://firebase.google.com/docs/hosting/manage-cache)).
- Requests to a Cloud Run service time out after 60 seconds
  ([Cloud Run rewrites](https://firebase.google.com/docs/hosting/cloud-run)).
- Budget alerts do not cap usage or charges, so there is no hard spending cap
  ([pricing and quotas](https://firebase.google.com/docs/hosting/usage-quotas-pricing)).

## Decision

Use option A: a Cloud Run domain mapping with DNS only. It is the least machinery, costs
nothing, and the certificate is Google's to issue and renew.

**Accepted trade-off.** Measured on 2026-11-09 with a Python TLS probe, `next.kphutt.com`
offers TLS 1.0, 1.1, 1.2 and 1.3. `kphutt.com` on Pages and the `*.run.app` address refuse
1.0 and 1.1. So moving the apex to option A means offering two old protocol versions the
site refuses today. We accept that for now. The pages are public and static, nothing
secret crosses the connection, and every modern browser negotiates TLS 1.2 or 1.3. Scanners
that cap the grade for TLS 1.0 and 1.1 will report it.

**To revisit.** Revisit by 2027-02-10 (three months). GitHub Pages was retired after the
cutover, so this review no longer gates anything; it is a date to look at the question again.
The TLS question is not closed.

**Why not the others, for now**

- B (Cloudflare proxy in front of the mapping): the certificate renewal path can break
  silently after about 90 days, the failure `AGENTS.md` already records.
- C (load balancer): the TLS floor is configurable, but it costs about $18 a month, which
  breaks the $0 fixed-cost constraint.
- D (Cloudflare Worker): free and gives a TLS 1.2 floor, but past 100,000 requests a day
  requests fail, and it adds a script to maintain and a second place for configuration.
- E (Firebase Hosting in front): free with TLS 1.2 or newer, but it has a second set of
  tools, puts a CDN in the path, and has limits on cookies, request time and spend (see
  option E).

### Measured on `next.kphutt.com`, 2026-11-09

- **Smoke test:** `scripts/smoke.py https://next.kphutt.com` passed.
- **Certificate:** Google-managed, issued by Google Trust Services (WR3).
- **TLS versions offered:** 1.0, 1.1, 1.2, 1.3.
- **Request logs:** three requests with a made-up user agent and referrer were found in
  the `site-requests` log bucket. Each showed the real user agent, the real referrer and a
  client address of an ordinary visitor kind (IPv4), not a Google or Cloudflare address.
  The address is not recorded here, because this file is public.
- **Browser:** headless Chromium loaded the home page and `/posts/brochure/`. Both
  answered 200, with zero console messages and zero CSP violations. Every request went to
  `https://next.kphutt.com`, nothing else.
- **Mozilla HTTP Observatory:** grade A+, score 140, 12 of 12 tests passed.
- **SSL Labs** (2026-11-10, results not published): grade B on all 8 addresses; TLS 1.0,
  1.1, 1.2 and 1.3 offered; no other warnings.

## The apex

`kphutt.com` itself cannot hold a CNAME in standard DNS, and the two live options treat it
differently.

- **A:** the mapping for an apex hands out a fixed set of A and AAAA records (Google front
  end addresses), and those are what go in. Cloudflare's CNAME flattening could instead
  answer for the apex with the addresses behind `ghs.googlehosted.com`, but Google's
  instructions name the A and AAAA records, and a flattened answer is not something its
  certificate check is documented to accept. So A uses the A and AAAA records, DNS only.
  `www` takes the CNAME to `ghs.googlehosted.com`.
- **D:** Cloudflare answers for the name itself, so the apex record only has to exist and
  be proxied. Its target is never contacted, because the Worker route on `kphutt.com/*`
  handles every request. A placeholder such as `AAAA 100::` (a discard address) makes that
  explicit. Flattening does not come into it.

Either way, `www` keeps redirecting to the apex as it does on Pages: under A the server does
it by host name, under D a Cloudflare redirect rule does.

## Client addresses in the request logs

Measured under A on 2026-11-09: the Cloud Run request log shows the visitor's address,
referrer and user agent, because nothing sits in front. Under B or D it would show
Cloudflare's address instead; Cloudflare sends the visitor's address in `CF-Connecting-IP`
and `X-Forwarded-For`, which only static-web-server's own log can record.

## Sources

- Cloud Run, mapping custom domains: https://docs.cloud.google.com/run/docs/mapping-custom-domains
- Cloud Run known issues (domain mapping latency): https://cloud.google.com/run/docs/issues
- Cloud Run HTTPS requests (HTTP is redirected to HTTPS): https://cloud.google.com/run/docs/triggering/https-request
- Load balancer SSL policies: https://cloud.google.com/load-balancing/docs/ssl-policies-concepts
- Load balancer pricing: https://cloud.google.com/load-balancing/pricing
- Firebase Hosting with Cloud Run (rewrites, 60 second timeout): https://firebase.google.com/docs/hosting/cloud-run
- Firebase Hosting cache and cookies (`__session`): https://firebase.google.com/docs/hosting/manage-cache
- Firebase Hosting pricing and quotas (budget alerts do not cap spend): https://firebase.google.com/docs/hosting/usage-quotas-pricing
- Cloudflare Origin Rules (Host header override is Enterprise-only): https://developers.cloudflare.com/rules/origin-rules/
- Cloudflare Minimum TLS Version: https://developers.cloudflare.com/ssl/edge-certificates/additional-options/minimum-tls/
- Cloudflare request headers sent to the origin: https://developers.cloudflare.com/fundamentals/reference/http-headers/
- Community report, certificate expired behind the proxy and would not reissue: https://discuss.google.dev/t/google-cloud-run-certificate-expired-then-new-provisioning-never-completes/130516
