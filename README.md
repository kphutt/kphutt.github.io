# kphutt.com

Personal website for Karsten Huttelmaier. Built with [Hugo](https://gohugo.io/) and the [PaperMod](https://github.com/adityatelange/hugo-PaperMod) theme.

## Deployment

Push to `main` triggers a GitHub Actions workflow that builds the site and deploys to GitHub Pages.

## Local development

```bash
mise install                      # the pinned Hugo, from mise.toml
mise exec -- hugo server -D       # dev server with drafts enabled
python scripts/local_verify.py    # the CI build check, run locally
```

## License

Two licences, split by what the files are:

- **Writing** — everything under `content/` — is licensed under
  [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/)
  (CC BY 4.0). You may share and adapt it for any purpose, including commercially, as long as
  you credit the author and link back to the original. The full text is in
  [`content/LICENSE`](content/LICENSE).
- **Code** — templates, scripts, configuration and workflows, everything outside `content/` —
  is licensed under the [Apache License 2.0](LICENSE).

The PaperMod theme in `themes/PaperMod` is a separate project under its own MIT licence.
