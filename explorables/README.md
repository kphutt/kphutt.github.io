# Explorables

Standalone interactive pages, served at `https://explorables.kphutt.com/`. Each one can run
its own JavaScript, so they live on their own origin and `kphutt.com` keeps
`script-src 'none'`. Why, and how they are built and deployed: `AGENTS.md`, "Explorables".

This folder is copied into the image as it is, except every `README.md`, which is for the
repository and is never served.

## Adding one

1. Make a folder `explorables/<slug>/`, with a slug of lowercase letters, digits and
   hyphens. It is the page's address: `https://explorables.kphutt.com/<slug>/`.
2. Put in it an `index.html`, its own script, style, image and font files, and a
   `README.md` that says what the page is and where its parts came from (a font's licence,
   for one).
3. Run `python scripts/local_verify.py`. It builds the folder and runs
   `scripts/check_explorables.py`, which fails if any of the rules below breaks.

## Rules

- Every page has `<meta name="robots" content="noindex, nofollow">`.
- Scripts and styles are files, loaded with `<script src>` and `<link rel="stylesheet">`.
  No inline `<script>` or `<style>`, no `style=` or `on...=` attributes, no `javascript:`
  URLs.
- Everything a page loads comes from this origin, by a relative URL. That includes fonts:
  bundle them, with their licence.
- No network calls. The policy has no `connect-src`, so `fetch` and the like are blocked.
- Trusted Types are required. Build the page with DOM methods (`textContent`,
  `createElement`) rather than by assigning strings to `innerHTML`.
- No dot-files.
