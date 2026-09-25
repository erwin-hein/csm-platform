# Shearwater CSM/Engagement Platform — proof of concept

The spec is [`CLAUDE.md`](CLAUDE.md). This PoC builds a slice of it: the Client/Engagement split,
the generalized Deliverables engine, the client portal, the CRM slice (opportunities, products,
pipeline and forecast), and module-based access control, with the events spine underneath. None of
the integrations, LLM features, rules engine or digests are built.

## What's here

| Area | Where |
|---|---|
| Schema (Alembic, Postgres) | `alembic/versions/0001_poc_core_schema.py`. PoC tables only, plus the `quickstart`/`migration` reference data |
| ORM models | `app/models.py` |
| Events spine | `app/events.py`. `emit()` plus the `@mutation` guard: a service function that writes without emitting its declared event raises. The DB rejects UPDATE/DELETE on `events` via a trigger |
| Access rules | `app/access.py`. Module access from access roles (`use`/`manage` per module), then record access: engagement memberships, opportunity ownership. Admin screen in `app/web/admin.py` |
| CRM | `app/services/opportunities.py` (opportunities, products, line items, the won → engagement bridge), `app/services/forecast.py` (pipeline, forecast, movement), pages in `app/web/crm.py` |
| Business logic | `app/services/`. No rendering and no commits in here |
| Web (Jinja2 + htmx) | `app/web/`, `app/templates/`, `app/static/` (htmx is vendored, no CDN) |
| Auth | `app/web/auth.py`. Google OAuth limited to `@shearwaterdata.com` (checked server-side), `ADMIN_EMAILS` bootstrap, signed httponly session cookie. `app/web/csrf.py` does the Origin/Referer check |
| Seed data | `app/seed.py`. Runs through the real service functions, so seeded rows emit events too |
| Tests | `tests/`. One invariant per file |
| Deploy / local run / CI | `render.yaml` (Blueprint), `scripts/start.sh`, `Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml` (tests on every push; publishes the image on merge to `main`) |

## Running locally

There's no pre-built image to pull. Clone the repo and build it locally.

### With Docker (recommended: no Python or Postgres install needed)

Requires Docker Desktop (or Docker Engine with Compose v2).

```bash
git clone https://github.com/erwin-hein/csm-platform.git
cd csm-platform
docker compose up --build
# → http://localhost:8000/demo-login  (passcode: demo)
```

The first start migrates the database and seeds the demo data. Restarts keep your data and skip seeding.

- **Stop:** Ctrl+C, or `docker compose down`.
- **Start over with fresh demo data:** `docker compose down -v` (deletes the database volume), then `docker compose up`.
- **Pick up code changes:** `git pull`, then `docker compose up --build`.
- **Skip the local build:** every merge to `main` publishes a tested image to `ghcr.io/erwin-hein/csm-platform:latest` (see `.github/workflows/ci.yml`). Run `docker compose pull web && docker compose up` to use it. If the package is private, first run `docker login ghcr.io -u <github-username>` with a personal access token that has the `read:packages` scope. A plain `docker compose up` tries the published image first and falls back to building locally.
- **Configuration:** none required. `docker-compose.yml` already holds every setting the demo needs. For the optional ones (`ADMIN_EMAILS`, Google OAuth), copy `.env.example` to `.env` and fill it in.
- **"port is already allocated" / "address already in use"** means something else on your machine already has port 8000. Set `APP_PORT=8001` (or any free port) in `.env` and open `http://localhost:8001` instead.
- **"no configuration file provided: not found"** means Docker can't see `docker-compose.yml` in the current folder. Run the command from the repo root, and make sure your checkout actually contains that file.

### Without Docker

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/) and a local Postgres.

```bash
uv sync
createdb csm && createdb csm_test
export DATABASE_URL=postgresql://postgres@127.0.0.1:5432/csm
uv run alembic upgrade head
uv run python -m app.seed               # --reset wipes and reseeds
DEV_LOGIN_PASSCODE=demo SESSION_HTTPS_ONLY=false uv run uvicorn app.main:app --reload
# → http://127.0.0.1:8000/demo-login  (passcode: demo)
uv run pytest                          # uses TEST_DATABASE_URL, default .../csm_test
```

### Environment variables

| Var | Purpose |
|---|---|
| `DATABASE_URL` | Postgres. `postgres://` and `postgresql://` URLs are both accepted |
| `SECRET_KEY` | Signs the session cookie |
| `ADMIN_EMAILS` | Comma-separated. A listed email becomes admin on its first login; everyone else starts with the default access roles (Delivery) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google OAuth web client. The redirect URI is `https://<host>/auth/google/callback` |
| `ALLOWED_DOMAIN` | Default `shearwaterdata.com` |
| `DEV_LOGIN_PASSCODE` | Turns on `/demo-login`. Leave unset to disable it |
| `SESSION_HTTPS_ONLY` | Default `true`. Set to `false` for plain-http local dev |
| `SEED_DEMO_DATA` | `true` makes `start.sh` seed an empty database (Render) |

## Deploying to Render

1. In Render, open **New → Blueprint**, pick this repo and branch. It creates `csm-platform-db` (Postgres) and `csm-platform` (web).
2. When prompted, set `ADMIN_EMAILS`. The Google vars can stay blank for the demo.
3. On start the service migrates, seeds (only if the DB is empty) and serves. Get the generated `DEV_LOGIN_PASSCODE` from the service's Environment tab.
4. For real Google sign-in: create an OAuth web client in Google Cloud (Internal app type, on the Shearwater Workspace), add the redirect URI above, then set both Google vars.

## Demo walkthrough

Sign in at `/demo-login` as **Morgan Ellis (admin)**.

1. **Managing projects.** On Portfolio, click **+ Add Client**, then **+ Add Engagement**. The type picker shows both stage vocabularies and deliverable structures. On the new engagement, use **+ Add Phase**, then **+ Milestone** under it. Asking for a milestone with no phase, or a tile under a phase, shows the server's refusal inline. Open a deliverable to add a dependency (Waits on…) or flag it blocked.
2. **Assigning people.** Open **Team**. **By person** shows each analyst's and contractor's load (owned/collaborating engagements, open items, estimated open hours, overdue). Allocate someone to an engagement from their card. **By engagement** highlights *Harbor & Pine Power BI → Omni Migration* as having no team. The same panel sits on every engagement page.
3. **Two different engagement types.** Each board shows one big card per engagement: active stage(s), progress, next deadline or overdue count, how long it's been running, items in UAT, and the team. Use **Group by** / **Sort by** to rearrange them. QuickStart stages are set by hand, and its board groups by stage by default. Migration stages follow the phases, so Bluefin shows both *Semantic-layer Parity* and *Dashboard Build* as active; grouped by stage, it appears under both. On Bluefin's *Clinical Operations Workbook*, 99 tiles are tracked mostly by count (**Edit counts**) with only the 2 troublesome tiles listed. Open *Northwind QuickStart — 2026* (a flat curriculum list), then *Bluefin Tableau → Omni Migration* (two trees: Phases → Milestones and Dashboards → Tiles, with rollups). Bluefin's *Denial rate by payer* shows **Blocked**, *Bed occupancy trend* shows **Maybe unblocked**, and *Parity sign-off* shows **Waiting**. All three are derived from blocker edges plus the manual flag.
4. **Access control.** Sign out and sign in as **Aisha Bello (analyst)**. She sees Cobalt's Migration but not Cobalt's QuickStart on the same client, and gets a 404 on anything else. If you assigned her to Harbor & Pine in step 2, it shows up now.
5. **Working a deliverable.** On an engagement page, finished items start collapsed. The 💬 button on any deliverable opens its activity log and lets you comment without leaving the page. Items that simply come after unfinished work show a quiet "after X"; only blocks and held-up in-progress work are flagged loudly.
6. **The client side.** On *Bluefin Tableau → Omni Migration*, the **Client view** tab shows exactly what the client's project lead sees: dashboards and tiles only, never phases or milestones. The **Client access** panel on the engagement page lists the two client users and their scope; admin, ops or the engagement's owner can invite, rescope or revoke. In any tile's 💬 pop-up, *Internal notes* and the *Client thread* are separate tabs with separate colours.
7. **As the client.** Sign in at `/demo-login` as **Dr. Hannah Cho** (Bluefin project lead): she sees every dashboard and tile. Then sign in as **Marcus Reid** (Bluefin QA, assigned only): he sees only the tiles he owns, with their dashboards shown for context. *Daily census KPI* is in UAT and carries his earlier rejection plus Tomás's reply, so he can review it again. *Bed occupancy trend* isn't in UAT yet, so he can comment but not give a verdict.
8. **The pipeline.** Sign in as **Nadia Osei (Sales lead)**. **Pipeline** shows a column per stage with $ totals and weighted totals; move a deal with the stage picker on its card (Closed Lost asks for a reason). The banner flags *Cobalt Store Ops Dashboards — Phase 2*: won, but no engagement delivers it yet. Open *Juniper Tableau → Omni Migration* to see the stage path, its two products (the amount is always their sum), and the probability/forecast overrides. **Forecast** shows the quarter (or month) by owner and forecast category, the outlook for the next periods, and recent movement, including *Northwind Creator Training*'s slipped close date.
9. **The handoff.** As Morgan, the Portfolio shows the same won-but-undelivered flag. Open the opportunity and use **+ Create engagement from this**: client, name and type come prefilled from the product.
10. **Module access.** As Morgan, open **Access**: roles × modules with `use`/`manage`, and who holds which roles. Every combination works: **Rafael Costa** (Sales) lands on the pipeline and never sees an engagement; **Aisha Bello** (Delivery) gets a 404 on the pipeline; **Priya Raman** (Delivery + Sales) has both; **Tomás Alvarez** (Delivery + Resourcing) can allocate people from **Team**. Give a role a new grant and it takes effect on the next page load.
11. **The spine.** Every step above shows up in **Event log** (admin, or `manage` on a module), in the same transaction as the write.

Seeded users (all fictional), with their access roles: Morgan Ellis (admin); Elena Park (Operations); Priya Raman (Delivery + Sales); Tomás Alvarez (Delivery + Resourcing); Aisha Bello (Delivery); Jordan Kim (Delivery, contractor); Rafael Costa (Sales); Nadia Osei (Sales lead). Client users on Bluefin: Dr. Hannah Cho (project lead, full scope) and Marcus Reid (QA, assigned only). Prospects: Atlas Freight and Juniper Biotech.

## PoC judgment calls to review

These are small calls I made without you. Each one can be reverted. The larger spec gaps it surfaced were all signed off and now live in CLAUDE.md §2/§3 (logged in §6).

**Tooling (not in the spec)**
- **uv** for dependency management (`pyproject.toml` + `uv.lock`).
- **SQLAlchemy 2.0 (sync) + psycopg 3.** Sync keeps it simple; FastAPI runs sync routes in a threadpool.
- **Hand-written first migration**, not autogenerated, so it can carry the trigger, CHECK constraints and reference-data seeds.
- **Starlette `SessionMiddleware`** for the signed cookie. The OAuth flow itself uses plain `httpx`, with no auth library.
- **htmx vendored** into `app/static/`. No CDN and no build step.
- Layout is `app/{services,web,templates,static}`, with routes kept thin over services.

**Schema additions beyond §3.** All are enforcement only; no new concepts.
- `deliverable_kinds.parent_kind` says which kind may parent which. The ≤2-level trees needed it (now in CLAUDE.md §3).
- A composite FK `deliverables(engagement_id, engagement_type_key) → engagements(id, type_key)`, so the denormalized type key can't drift.
- CHECK constraints on `pipeline_status`, `engagements.status`, no self-parent, and no self-blocker.
- A partial unique index allowing one `owner` membership per engagement. That membership *is* the owner; there is no owner column (now in CLAUDE.md §3/§6).
- A trigger that makes `events` append-only.
- `created_at` also gets a Python-side default, so rows created in one transaction still sort in creation order.
- Out-of-scope columns that §3 puts on `engagements` (`slack_channel_id`, `internal_slack_channel_id`, `harvest_project_id`, `expected_scope`, `health`) are in the schema to match the spec, but nothing reads or writes them.

**Behaviour**
- **Demo login** (`/demo-login`, off unless `DEV_LOGIN_PASSCODE` is set). Without it, seeded users can't be logged into, because they have no Google accounts. It goes through the same user-resolution path as Google, so the domain rule and `ADMIN_EMAILS` still apply. It's on in `render.yaml`, behind a generated passcode.
- **Permissions** (now in CLAUDE.md §3 Identity & access): creating engagements needs `engagements:manage`; managing teams needs `engagements:manage` or `team:manage`; clients, aliases and contacts can be added with `engagements:manage` or `opportunities:use`. Editing deliverables and stages is open to owners, collaborators and `engagements:manage`. Viewers are read-only. A non-member gets a 404, never a 403, so an engagement's existence doesn't leak; so does a module you have no access to.
- **CRM calls made without you** (all easy to change): closing an opportunity sets its close date to today; probability/forecast overrides are ignored while closed and come back if it's reopened; only a Closed Won opportunity can be linked to an engagement; the product's "delivered as" type prefills the engagement form; the forecast's movement list covers the last 14 days across all periods; an admin can't remove their own admin flag.
- **Deliverable assignee** must be on the engagement's team (or be ops/admin).
- **Blockers** must be on the same engagement. Cycles are rejected. Flagging something blocked requires a reason.
- **Event log page** (`/events`, ops/admin). This is a read view of the spine, not a consumer.
- `deliverable_completed` is emitted, alongside `deliverable_updated`, whenever a deliverable moves to `done`.
- **Render plans are `free`**, to avoid spending without sign-off. Free Postgres expires after 30 days.
