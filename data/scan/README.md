# Research scan targets

Put your hand-compiled list here as `targets.csv` (PRD §16). It is **git-ignored on purpose**:
a list of business websites is exactly the per-site information the findings must never
publish, so keep it off the public repo.

```csv
url,category,source
https://www.example-bakery.com/,bakery,Springfield Chamber of Commerce
example-florist.com,florist,Springfield Chamber of Commerce
```

- `url`: a landing page or home page. Bare domains are fine (https:// is assumed).
- `category`: free text, used for the by-category table (categories with fewer than 5 loaded
  sites are left out of the report).
- `source`: where you found it (the directory's name). Only counts per source are published.

Only one page per registrable domain is loaded; extra rows for the same domain are skipped
and counted. Check each directory's terms before copying from it, and don't scrape.

Run it (with `make dev` running, so workers pick up the jobs):

```bash
make scan NAME=fall-2026        # vets targets, checks robots.txt, queues one page load per domain, waits
make findings NAME=fall-2026    # writes docs/findings.md and docs/findings/*.png
```
