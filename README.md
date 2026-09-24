# Shearwater CSM/Engagement Platform — proof of concept

The spec is [`CLAUDE.md`](CLAUDE.md). This PoC builds one slice of it: the Client/Engagement split,
the generalized Deliverables engine, and multi-role access control, with the events spine
underneath. None of the integrations, LLM features, portal, rules engine or digests are built.

## What's here

| Area | Where |
|---|---|
| Schema (Alembic, Postgres) | `alembic/versions/0001_poc_core_schema.py`. PoC tables only, plus the `quickstart`/`migration` reference data |
| ORM models | `app/models.py` |
| Events spine | `app/events.py`. `emit()` plus the `@mutation` guard: a service function that writes without emitting its declared event raises. The DB rejects UPDATE/DELETE on `events` via a trigger |
| Access rules | `app/access.py`. Membership-based visibility, ops/admin bypass |
| Business logic | `app/services/`. No rendering and no commits in here |
| Web (Jinja2 + htmx) | `app/web/`, `app/templates/`, `app/static/` (htmx is vendored, no CDN) |
| Auth | `app/web/auth.py`. Google OAuth limited to `@shearwaterdata.com` (checked server-side), `ADMIN_EMAILS` bootstrap, signed httponly session cookie. `app/web/csrf.py` does the Origin/Referer check |
| Seed data | `app/seed.py`. Runs through the real service functions, so seeded rows emit events too |
| Tests | `tests/`. One invariant per file |
| Deploy / local run | `render.yaml` (Blueprint), `scripts/start.sh`, `Dockerfile` and `docker-compose.yml` |

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
- **Optional settings:** `ADMIN_EMAILS` and the Google OAuth vars can go in a `.env` file next to `docker-compose.yml`.

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
| `ADMIN_EMAILS` | Comma-separated. A listed email becomes `admin` on its first login; everyone else starts as `analyst` |
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
2. **Assigning people.** Open **Team assignment**. *Harbor & Pine Power BI → Omni Migration* is highlighted as having no team. Assign someone as owner. The same panel sits on every engagement page.
3. **Two different engagement types.** The **QuickStart board** has 8 stage columns and the **Migration board** has 6. Open *Northwind QuickStart — 2026* (a flat curriculum list), then *Bluefin Tableau → Omni Migration* (two trees: Phases → Milestones and Dashboards → Tiles, with rollups). Bluefin's *Denial rate by payer* shows **Blocked**, *Bed occupancy trend* shows **Maybe unblocked**, and *Parity sign-off* shows **Waiting**. All three are derived from blocker edges plus the manual flag.
4. **Access control.** Sign out and sign in as **Aisha Bello (analyst)**. She sees Cobalt's Migration but not Cobalt's QuickStart on the same client, and gets a 404 on anything else. If you assigned her to Harbor & Pine in step 2, it shows up now.
5. **The spine.** Every step above shows up in **Event log** (admin/ops), in the same transaction as the write.

Seeded users (all fictional): Morgan Ellis (admin); Priya Raman, Tomás Alvarez, Aisha Bello (analysts); Jordan Kim (contractor).

## PoC judgment calls to review

These are small calls I made without you. Each one can be reverted. The larger spec gaps are in CLAUDE.md §5.

**Tooling (not in the spec)**
- **uv** for dependency management (`pyproject.toml` + `uv.lock`).
- **SQLAlchemy 2.0 (sync) + psycopg 3.** Sync keeps it simple; FastAPI runs sync routes in a threadpool.
- **Hand-written first migration**, not autogenerated, so it can carry the trigger, CHECK constraints and reference-data seeds.
- **Starlette `SessionMiddleware`** for the signed cookie. The OAuth flow itself uses plain `httpx`, with no auth library.
- **htmx vendored** into `app/static/`. No CDN and no build step.
- Layout is `app/{services,web,templates,static}`, with routes kept thin over services.

**Schema additions beyond §3.** All are enforcement only; no new concepts.
- `deliverable_kinds.parent_kind` says which kind may parent which. The ≤2-level trees needed it (see §5).
- A composite FK `deliverables(engagement_id, engagement_type_key) → engagements(id, type_key)`, so the denormalized type key can't drift.
- CHECK constraints on `pipeline_status`, `engagements.status`, no self-parent, and no self-blocker.
- A partial unique index allowing one `owner` membership per engagement. That membership *is* the owner; there is no owner column (now in CLAUDE.md §3/§6).
- A trigger that makes `events` append-only.
- `created_at` also gets a Python-side default, so rows created in one transaction still sort in creation order.
- Out-of-scope columns that §3 puts on `engagements` (`slack_channel_id`, `internal_slack_channel_id`, `harvest_project_id`, `expected_scope`, `health`) are in the schema to match the spec, but nothing reads or writes them.

**Behaviour**
- **Demo login** (`/demo-login`, off unless `DEV_LOGIN_PASSCODE` is set). Without it, seeded users can't be logged into, because they have no Google accounts. It goes through the same user-resolution path as Google, so the domain rule and `ADMIN_EMAILS` still apply. It's on in `render.yaml`, behind a generated passcode.
- **Permissions:** creating clients and engagements, adding aliases and contacts, and managing teams are ops/admin only. Editing deliverables and stages is open to owners, collaborators and ops/admin. Viewers are read-only. A non-member gets a 404, never a 403, so an engagement's existence doesn't leak.
- **Deliverable assignee** must be on the engagement's team (or be ops/admin).
- **Blockers** must be on the same engagement. Cycles are rejected. Flagging something blocked requires a reason.
- **Event log page** (`/events`, ops/admin). This is a read view of the spine, not a consumer.
- `deliverable_completed` is emitted, alongside `deliverable_updated`, whenever a deliverable moves to `done`.
- **Render plans are `free`**, to avoid spending without sign-off. Free Postgres expires after 30 days.
