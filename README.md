# tag-monitor

Know the moment your Meta Pixel or Google tags stop firing on your ad landing pages.

tag-monitor loads your landing pages in a real headless browser on a schedule, watches the network requests your tags really send, and emails you once, in plain English, when something that used to work breaks, then again when it's fixed.

> Work in progress: a portfolio project built milestone by milestone. The spec is [docs/PRD.md](docs/PRD.md), design decisions are in [docs/decisions.md](docs/decisions.md), and each milestone's report is in [docs/milestones/](docs/milestones/).

## Quickstart

Requirements: Docker (with Compose v2) and `make`.

```bash
git clone <this repo> tag-monitor && cd tag-monitor
make dev          # first run copies .env.example to .env, then builds and starts everything
```

| Service | URL |
|---|---|
| Dashboard | http://localhost:3001 |
| API health | http://localhost:8001/api/health |
| Mailpit (captured emails) | http://localhost:8025 |
| MinIO console | http://localhost:9001 |

Other targets: `make test`, `make lint`, `make fmt`, `make down`. Run `make help` for the full list.

### Try the demo

The dev stack includes a demo landing page, "Bean There Coffee", with a Meta Pixel and GA4.

1. Open http://localhost:3001, sign up, and add `http://beanthere.demo/` as a page.
2. `make demo-break-pixel`, then click **Check now**. After a confirmation re-check (60 s in dev) an alert lands in Mailpit (http://localhost:8025).
3. `make demo-fix-pixel`, then **Check now** again for the recovery email.

`scripts/demo_walkthrough.py` does all of this in a browser and can record a video.
