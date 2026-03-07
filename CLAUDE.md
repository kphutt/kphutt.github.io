# CLAUDE.md — kphutt.github.io

## What This Is
Personal website for Karsten Huttelmaier. Hugo static site deployed to GitHub Pages.

## Architecture
- Hugo with PaperMod theme
- Homepage: `content/_index.md` (custom layout renders as content page, not post list)
- Notes: `content/notes/` (individual markdown files, rendered as post list)
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
- New note: create `content/notes/your-note.md` with front matter (title, date, description)
- Edit homepage: edit `content/_index.md`
- That's it. Push to main, GitHub Actions deploys.
