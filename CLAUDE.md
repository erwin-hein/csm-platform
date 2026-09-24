# Corporate CSM/Engagement Platform — Living Architecture Spec

Status: **v1 design complete (2026-09-24) — entering development.** Every item that was tracked as Open (§5) is now resolved into §2/§3. This file remains the source of truth going forward, including during development — it supersedes anything said in chat, and any new design question that surfaces mid-build gets folded back in here the same way everything above was (see §5), never left to live only in a conversation or a commit message.

**How to use this doc**: Section 2 is the settled foundation. Section 3 is the concrete schema — the actual source of truth for implementation. Section 4 is scope explicitly cut for v1 (not forgotten, just sequenced later). Section 5 tracks anything still undecided (empty at v1 completion, see above). Section 6 is a dated decision log for traceability ("why did we do it this way").

---

## 1. Vision (condensed)

An internal corporate service (Shearwater Data) for analysts to track, manage, and update the status of client engagements — a CSM assistant, built for multi-user async collaboration from day one, not retrofitted onto a single-user tool.

Reference/cherry-pick source: a colleague's (Hao's) existing single-user Flask+SQLite tool (`csm-assistant`), which automates a QuickStart-focused CSM workflow (Fathom → Slack → Gmail → Harvest, LLM-drafted follow-ups). It's genuinely strong on integration logic and hard-won operational lessons, but has two structural flaws this rebuild fixes deliberately:

1. **Single flattened entity** for "client" conflated client identity with engagement-specific state. This rebuild splits **Client** (stable org identity) from **Engagement** (typed, time-bound unit of work) as first-class, independent entities.
2. **Workflow idiosyncratic to one person's habits**, with near-nonexistent onboarding — unusable by anyone else without heavy hand-holding. This rebuild is designed for multiple analysts, part-time contractors, an ops/admin role, and eventual external client viewers, with real role-based access control and an eventual onboarding flow.

An earlier attempt at this same rebuild stalled when a developer resource fell through. A separate inventory document (compiled from a five-way sweep of Hao's tool, "the platform map") lists what's worth porting as business logic, what's worth re-implementing as a pattern, what needs rebuilding differently for a multi-profile platform, and what to leave behind — that document has been a primary reference throughout this design conversation.

---

## 2. Foundational decisions (settled)

| # | Decision | Notes |
|---|---|---|
| 1 | **Client vs Engagement split** | `Client` = stable org identity + contact roster only. `Engagement` = typed, time-bound unit that owns everything ephemeral by default: meetings, Slack channel, Harvest project mapping, deliverables. Contacts are Client-scoped (low UX cost to bubble up); almost everything else defaults to Engagement-scoped (keep engagement views clean/contained). |
| 2 | **EngagementType as a contract** | Each type declares a stage vocabulary, a `storage_mode` (`jsonb` for immature/low-volume types, `graduated` to dedicated tables once a type needs real relational math), and its allowed deliverable `kind`s. New engagement types register, they don't fork code. |
| 3 | **Deliverables: one generalized tracking engine** | A single tree/pipeline structure (blockers, pipeline_status, enforced `kind` per type via a lookup table) serves any engagement type with trackable work units — migration phases/milestones/dashboards, QS curriculum modules, a retainer's initiatives. Replaces what would otherwise be N bespoke per-type tracking tables. |
| 4 | **Meeting action items stay separate from deliverables** | Different origin (machine-extracted from transcripts vs. deliberately scoped), different lifecycle (flat open/done/dismissed vs. a real pipeline+blockers), different volume/noise profile. Bridged by an optional `action_items.promoted_to_deliverable_id` FK, never merged. |
| 5 | **Event bus as the spine** | Append-only `events` table. Every state-mutating service function emits an event in the same transaction via one shared helper — not ad hoc per feature. Derived effects (health recompute, digests, checklists, notifications) are handlers subscribed by `event_type`, never direct cross-service calls into another module's tables. |
| 6 | **Users & roles** | `user_type` (`internal` \| `client_external`). Internal `internal_role`: `analyst` (renamed from "CSM"), `contractor` (part-timers), `ops`, `admin`. Visibility/ownership lives on `engagement_memberships` (`owner`/`collaborator`/`viewer`), **not** at the Client level — a client-external viewer gets an explicit per-Engagement grant, never implicit access to everything under a Client. `ops`/`admin` bypass membership checks for portfolio-wide reporting. |
| 7 | **Health** | Computed/cached per Engagement (`engagement_signals`), never hand-authored. Client-level health is a read-time rollup over its active Engagements' signals, never its own stored column (so one client with a thriving retainer and a stalled migration doesn't collapse into one misleading number). |
| 8 | **Meeting resolution is two independently-nullable stages** | `meetings.client_id` then `meetings.engagement_id`. Client match: canonical name scan → curated alias fallback → domain match against **existing** clients only (refuse to guess at every tier — ported from Hao's system almost verbatim). Engagement match: exactly one active Engagement on the matched client → auto-assign; zero or 2+ → leave null (lands in the unassigned-meetings backfill screen either way — no smart disambiguation logic needed given concurrent engagements per client are expected to be rare). |
| 9 | **Auto-discovery of new Clients/Engagements from sync: deferred out of v1** | See §4. Manual creation only, plus a manual "unassigned meetings" backfill/reassignment screen. An `ignored` flag on meetings stops permanently-irrelevant recurring calls (vendor stand-ups, etc.) from resurfacing every sync. |
| 10 | **Hosting** | Render (the corporation's existing account). |
| 11 | **Stack** | Python + **FastAPI** (not Flask). |
| 12 | **Frontend** | **Server-rendered Jinja2 templates + htmx** for partial-update interactivity, not a separate SPA. Reasoning: every UI pattern worth preserving from Hao's tool (async-off-page-load, job tray, undo-everywhere, inline click-to-edit) is achievable this way, none require a component framework; a single codebase/no build-pipeline matches every other "simple and fast to ship" choice made in this design; genuinely reversible later on a **per-page** basis (add a JSON API + build one page as a richer client) as long as business logic stays in service functions decoupled from the rendering layer — never a full rewrite if that decoupling is respected from the start. |
| 13 | **Database** | Postgres (self-hosted via Render's managed Postgres), not SQLite. BigQuery explicitly ruled out as the operational/OLTP store (wrong latency/cost/transaction model for a chatty CRUD app) — a plausible **future** downstream analytics sink fed *from* Postgres, not built now. |
| 14 | **Claude/LLM billing** | Single **org-level Anthropic API key** for the whole app (not per-user subscription OAuth à la Hao's `claude setup-token` hack, not per-user/per-workspace API keys) — chosen pragmatically given ops/finance uncertainty about how the company wants this billed. Per-user cost attribution kept independently in an app-level `llm_usage` log regardless of the shared credential, so this is reversible later (splitting into per-user/per-workspace keys) without touching anything above the credential-resolution layer. Two-tier model choice, per-feature budget/timeout discipline, and cache-first-never-on-GET are all explicitly ported principles from Hao's `llm.py`. |
| 15 | **LLM content generation is opt-in, per-user default + per-engagement override, per-kind** | See §3 `llm_content_preferences`. Default is **off** (a real spend-control lever, not a feature everyone must opt out of) — absence of a preference row cascades to "disabled," so a brand-new user starts with everything off until they explicitly enable specific kinds. |
| 16 | **Authentication** | Hand-rolled (not a managed provider like WorkOS), chosen for MVP speed since internal users are the near-term priority and external users are out of scope for now anyway. **Internal** (`analyst`/`contractor`/`ops`/`admin`): Google OAuth restricted to `@shearwaterdata.com`, verified server-side (the `hd` hint is not enforcement). **External** (`client_external`): passwordless email magic-link owned entirely by the app — deferred until the client-portal work begins (see §4), since it's the same piece of work as that feature. First-login bootstrap via an `ADMIN_EMAILS` env var seeding initial `internal_role='admin'` rows (avoids a chicken-and-egg problem); everyone else defaults to `analyst`. Session = signed httponly secure cookie holding the user id. CSRF via origin/referer check on state-changing routes, same principle as Hao's tool. Worth carrying over conceptually (not yet built): Hao's actor-vs-scope split for admin "act as another user" impersonation with audit trail. **Demo login**: a passcode-gated `/demo-login`, off unless `DEV_LOGIN_PASSCODE` is set, lets demos sign in as seeded users who have no Google account; it runs through the same user-resolution path (domain rule + `ADMIN_EMAILS` bootstrap) as Google sign-in. Admin impersonation above is the long-term replacement. |
| 17 | **OAuth/connections** (Calendar, Fathom, Harvest, Slack, Gmail) | Per-user-per-provider token storage, same shape as Hao's `connections` table, FK'd to real `users.id` instead of email strings. Gmail added for CSAT delivery (draft-only, `gmail.compose` scope, never a send scope). **Notion is the one exception** — org-level, not per-user (single admin-configured token, one shared company workspace being read), same reasoning as the single Claude API key. See §3 Knowledge base. |
| 18 | **Background jobs** | A real job queue (Celery/RQ-class), replacing Hao's LaunchAgent/cron/daemon-thread pattern — needed given multiple analysts' syncs running concurrently on a shared server rather than one person's Mac. |
| 19 | **Migration tooling** | Alembic (implied by "real Postgres migrations," not yet exercised in detail). |

---

## 3. Schema (current source of truth)

This is the accumulated, corrected schema — later refinements in this document supersede earlier mentions of the same table in chat.

### Identity & access

```sql
CREATE TYPE user_type AS ENUM ('internal', 'client_external');
CREATE TYPE internal_role AS ENUM ('analyst', 'contractor', 'ops', 'admin');

CREATE TABLE users (
  id UUID PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  display_name TEXT,
  user_type user_type NOT NULL,
  role internal_role,                 -- NULL for client_external
  llm_content_default BOOLEAN,        -- deprecated by llm_content_preferences, see below; kept here only as a note, not a real column
  status TEXT DEFAULT 'active',
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TYPE membership_role AS ENUM ('owner', 'collaborator', 'viewer');

CREATE TABLE engagement_memberships (
  engagement_id UUID REFERENCES engagements(id),
  user_id UUID REFERENCES users(id),
  role membership_role NOT NULL,
  PRIMARY KEY (engagement_id, user_id)
);
-- An engagement's owner IS its single 'owner' membership — there is no separate owner column (see §6).
CREATE UNIQUE INDEX uq_engagement_memberships_one_owner ON engagement_memberships (engagement_id) WHERE role = 'owner';
```

**Permission model** (internal users):
- **Read** an engagement and its deliverables: any membership role, or `ops`/`admin`.
- **Edit** deliverables and move stage/status: `owner` or `collaborator` membership, or `ops`/`admin`. `viewer` is read-only.
- **Create** clients and engagements, add aliases/contacts, and **manage memberships**: `ops`/`admin` only.
- A user with no visibility gets a 404, never a 403, so an engagement's existence doesn't leak; a `viewer` attempting an edit gets a 403.

> Note: `users.llm_content_default` as a flat boolean was an intermediate design, **superseded** by the per-kind `llm_content_preferences` table below. Do not implement the flat column.

### Client

```sql
CREATE TABLE clients (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  domains TEXT[] NOT NULL DEFAULT '{}',    -- inbound matching key
  created_at TIMESTAMPTZ DEFAULT now()
  -- NOTE: no confirmation_status column. Discovery/confirm-queue was deferred out of v1 (§4),
  -- so there is nothing to confirm — clients are only ever created explicitly.
);

CREATE TABLE client_aliases (
  id UUID PRIMARY KEY, client_id UUID REFERENCES clients(id),
  alias TEXT NOT NULL, alias_type TEXT NOT NULL     -- 'name' | 'acronym' | 'slack_slug'
);

CREATE TABLE client_contacts (
  id UUID PRIMARY KEY, client_id UUID REFERENCES clients(id),
  name TEXT, email TEXT, slack_user_id TEXT, title TEXT,
  source TEXT,                     -- 'manual' | 'slack_channel'
  status TEXT DEFAULT 'active'     -- 'active' | 'excluded', survives re-sync
);
```

### Engagement + type system

```sql
CREATE TABLE engagement_types (
  key TEXT PRIMARY KEY,                -- 'quickstart' | 'migration' | 'support_retainer' | ...
  display_name TEXT,
  storage_mode TEXT NOT NULL,          -- 'jsonb' | 'graduated'
  stage_vocab JSONB NOT NULL           -- ordered [{key,label}], drives board columns
);
```

**The two core types' vocabularies, drafted now** (a loose end flagged earlier in this document, resolved once PoC seed data made it concrete) — grounded in the reference tool's actual stage/kind vocabulary, not invented from scratch:

```sql
-- 'quickstart': call-driven curriculum, flat structure
INSERT INTO engagement_types VALUES ('quickstart', 'QuickStart', 'jsonb', '[
  {"key":"kickoff","label":"Kickoff"}, {"key":"dev_training","label":"Dev Training"},
  {"key":"codev","label":"Co-Dev"}, {"key":"creator_training","label":"Creator Training"},
  {"key":"wrapup","label":"Wrap-Up"}, {"key":"handed_off","label":"Handed Off"},
  {"key":"post_qs","label":"Post-QS"}, {"key":"dormant","label":"Dormant"}
]');

-- 'migration': coarse 6-stage skeleton. No stage is a "gate" — stages never block work in other stages (see §6).
INSERT INTO engagement_types VALUES ('migration', 'Migration', 'jsonb', '[
  {"key":"scoping","label":"Scoping"}, {"key":"access_setup","label":"Access & Setup"},
  {"key":"semantic_parity","label":"Semantic-layer Parity"}, {"key":"dashboard_build","label":"Dashboard Build"},
  {"key":"client_validation","label":"Client Validation"}, {"key":"golive_wrapup","label":"Go-live / Wrap-up"}
]');
```

```sql
CREATE TABLE engagements (
  id UUID PRIMARY KEY,
  client_id UUID REFERENCES clients(id),
  type_key TEXT REFERENCES engagement_types(key),
  name TEXT NOT NULL,                  -- "Omni Migration — 2026" etc, since a client can have several over time
  stage TEXT NOT NULL,                 -- validated app-side against the type's stage_vocab
  status TEXT DEFAULT 'active',        -- active | paused | complete | cancelled
  slack_channel_id TEXT,               -- engagement-scoped, ephemeral by design — client-facing channel
  internal_slack_channel_id TEXT,      -- engagement-scoped internal back-channel (never client-facing, never feeds client_contacts)
  harvest_project_id TEXT,             -- engagement-scoped, ephemeral by design
  expected_scope JSONB,                -- generalizes Hao's expected_phases
  health TEXT,                         -- cached, recomputed — never hand-written
  details JSONB DEFAULT '{}',          -- type-specific payload while storage_mode='jsonb'
  llm_content_override BOOLEAN,        -- deprecated by llm_content_preferences, see below; do not implement
  started_at TIMESTAMPTZ, ended_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
  -- NOTE: no confirmation_status column, same reasoning as clients.
  -- NOTE: no owner_user_id column — ownership is the engagement's single 'owner' membership (see §6).
);
```

> Note: `engagements.llm_content_override` as a flat boolean was likewise an intermediate design, **superseded** by `llm_content_preferences`. Do not implement the flat column.

### Deliverables — the generalized tracking engine

```sql
CREATE TABLE deliverable_kinds (
  engagement_type_key TEXT REFERENCES engagement_types(key),
  kind TEXT NOT NULL,
  display_name TEXT,
  parent_kind TEXT,                    -- which kind may parent this one; NULL = a root (top-level) kind
  PRIMARY KEY (engagement_type_key, kind),
  FOREIGN KEY (engagement_type_key, parent_kind) REFERENCES deliverable_kinds(engagement_type_key, kind)
);
-- A child kind requires a parent of exactly its parent_kind; a root kind forbids a parent. This is what
-- enforces the ≤2-level trees, and it lets the engagement view be driven by data (one section per root kind)
-- rather than per-type templates.
```

**Kinds for the two core types**, drafted alongside the stage vocab above:

```sql
-- migration: two parallel ≤2-level trees — phase→milestone, dashboard→tile (collapses what were separate tables in the reference tool)
INSERT INTO deliverable_kinds VALUES
  ('migration', 'phase', 'Phase', NULL), ('migration', 'milestone', 'Milestone', 'phase'),
  ('migration', 'dashboard', 'Dashboard', NULL), ('migration', 'tile', 'Tile', 'dashboard');

-- quickstart: flat curriculum coverage, no parent/child tree needed
INSERT INTO deliverable_kinds VALUES ('quickstart', 'module', 'Curriculum Module', NULL);
```

```sql
CREATE TABLE deliverables (
  id UUID PRIMARY KEY,
  engagement_id UUID REFERENCES engagements(id),
  engagement_type_key TEXT,            -- denormalized from the parent engagement, enables the FK below
  parent_id UUID REFERENCES deliverables(id),   -- self-ref, ≤2 levels (e.g. dashboard -> tile)
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  pipeline_status TEXT DEFAULT 'not_started',   -- not_started|in_progress|internal_validation|external_validation|done
  blocked BOOLEAN DEFAULT false, blocked_reason TEXT,
  not_applicable BOOLEAN DEFAULT false,
  internal_assignee_user_id UUID REFERENCES users(id),   -- who's doing the work (internal, typically)
  client_owner_user_id UUID REFERENCES users(id),        -- who owns client-side QA/validation/sign-off (typically client_external) — independent of the above, both nullable
  priority TEXT, target_date DATE,
  hours_estimated NUMERIC,
  created_at TIMESTAMPTZ DEFAULT now(),
  FOREIGN KEY (engagement_type_key, kind) REFERENCES deliverable_kinds(engagement_type_key, kind)
);

CREATE TABLE deliverable_blockers (
  blocked_id UUID REFERENCES deliverables(id),
  blocker_id UUID REFERENCES deliverables(id),
  PRIMARY KEY (blocked_id, blocker_id)
  -- app enforces: no self-loop, no cycle, same engagement; dep_state (below) is derived, never stored
);

```

**`dep_state`** — derived at read time from the manual `blocked` flag plus the blocker edges; "finished" means `pipeline_status='done'` or `not_applicable`:
- `blocked` — manually flagged, and either no blocker edges or at least one unfinished blocker.
- `maybe_unblocked` — manually flagged, but every blocker is finished: the flag is probably stale. Surfaced for a human to clear, never auto-cleared.
- `waiting` — not flagged, but at least one blocker is unfinished.
- `clear` — everything else.

Blocker edges only connect deliverables on the same engagement.

```sql
CREATE TABLE deliverable_activity (
  id UUID PRIMARY KEY, deliverable_id UUID REFERENCES deliverables(id),
  actor_user_id UUID REFERENCES users(id), kind TEXT, body TEXT, created_at TIMESTAMPTZ DEFAULT now()
  -- internal-only, never client-visible — see the Portal section for the deliberately separate client-facing channel
);

-- Client-facing comms and review, see Portal section for the full design:
CREATE TABLE deliverable_comments (
  id UUID PRIMARY KEY, deliverable_id UUID REFERENCES deliverables(id),
  author_user_id UUID REFERENCES users(id),   -- internal or client_external — bidirectional by design
  body TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE deliverable_client_reviews (
  id UUID PRIMARY KEY, deliverable_id UUID REFERENCES deliverables(id),
  reviewer_user_id UUID REFERENCES users(id),   -- a client_external user
  verdict TEXT NOT NULL,   -- 'accepted' | 'blocked' | 'rejected' — current verdict = latest row, derived, never overwritten in place
  created_at TIMESTAMPTZ DEFAULT now()
);
```

### Meetings & action items

```sql
CREATE TABLE meetings (
  id UUID PRIMARY KEY,
  client_id UUID REFERENCES clients(id),          -- nullable: NULL = no client match at all
  engagement_id UUID REFERENCES engagements(id),  -- nullable: NULL = client known, engagement unresolved/ambiguous/none yet
  ignored BOOLEAN DEFAULT false,                  -- set from the unassigned-meetings screen to stop permanent noise resurfacing
  external_source TEXT, external_id TEXT,
  title TEXT, occurred_at TIMESTAMPTZ,
  attendees JSONB, summary TEXT, transcript TEXT,  -- transcript write-once (append-only upsert, never clobbered by a thin refresh)
  raw_payload JSONB, session_type TEXT,
  handled_at TIMESTAMPTZ, handled_via TEXT,
  UNIQUE (external_source, external_id)
);

-- machine-extracted (Fathom), lifecycle: done/dismissed overlay, never becomes a typed nag
CREATE TABLE meeting_action_items (
  id UUID PRIMARY KEY, meeting_id UUID REFERENCES meetings(id),
  task TEXT, assignee TEXT, task_hash TEXT, status TEXT DEFAULT 'open'
);

-- typed/human-owned, separate lifecycle
CREATE TABLE action_items (
  id UUID PRIMARY KEY, engagement_id UUID REFERENCES engagements(id), meeting_id UUID REFERENCES meetings(id),
  owner_user_id UUID REFERENCES users(id), description TEXT, due_date DATE, priority TEXT,
  source TEXT, status TEXT DEFAULT 'open',
  promoted_to_deliverable_id UUID REFERENCES deliverables(id)  -- optional bridge: "promote this follow-up to a tracked deliverable"
);
```

**Service-layer resolution logic** (not schema, but load-bearing):
- Client match precedence: canonical name scan over `title || invitees` → curated alias fallback (`client_aliases`, requires exactly one unambiguous word-boundary hit) → domain match against existing clients' `domains[]`. Any tier failing entirely leaves `client_id` NULL — never guess.
- Engagement match: client known → exactly one `status='active'` Engagement on that client auto-assigns; zero or 2+ leaves `engagement_id` NULL.
- Attendee "is-me" filtering (only ingest calls the syncing user attended), deep-first-sweep (90d) vs incremental (7d) lookback window for a newly-connected user, append-only upsert (COALESCE guards, transcript write-once) — all ported from Hao's sync as-is.

### Signals & health

```sql
CREATE TABLE engagement_signals (
  engagement_id UUID PRIMARY KEY REFERENCES engagements(id),
  recency_days INT, channel_breakdown JSONB, cancels_30d INT, computed_at TIMESTAMPTZ
);
```
Client-level health = read-time rollup over active Engagements' signals, never stored.

### Slack integration

Two very different mechanisms, both per-user OAuth (not a bot token): **reads** feed health signals and the contact roster, almost entirely free of LLM cost; **writes** are the delivery mechanic for outbound client messages (post-call follow-ups and anything else a user sends to a client via the app).

**Reads — deterministic signal computation, not summarization.** Channel/DM/group-DM history is pulled to compute multi-channel recency (`engagement_signals.channel_breakdown`) — last attended call, client-facing channel activity, internal back-channel activity, DM/support-channel mentions, folded via `min()` into the one automatic health signal. This is pure math, zero LLM calls — genuinely useful specifically for engagement types where meaningful client interaction happens between calls (a support retainer, an ongoing partnership), less so for short call-driven engagements, and cheap enough that there's no real reason to gate it either way. There is deliberately **no standalone "summarize this Slack channel" LLM feature** — Slack content instead supplies raw context *into* already-planned generation features (a pre-call briefing's "recent Slack" section, a digest's context), never summarized on its own.

- **A read failure is never treated as genuine silence.** A throttled/failed `conversations.history` call must not be read as "the channel has gone quiet" — health computation keeps the prior persisted signal on a failed pull, and only trusts a successful-but-empty read as real quiet. (This exact conflation caused a false "stalled" flag on an actively-engaged client in the reference tool.)
- **Contact roster derivation unions across all of a Client's Engagements**, not just the active one — since contacts are Client-scoped but the channel they're derived from is Engagement-scoped, the roster-refresh job iterates every Engagement belonging to a Client and unions channel membership from each one's `slack_channel_id` into `client_contacts`. Only the client-facing channel feeds this — `internal_slack_channel_id` never does, since its members are staff by definition.
- **Staff-vs-client-contact filtering** when scanning channel membership: by domain, plus a "ubiquity" heuristic (a config threshold — appears in ≥N distinct client channels = probably internal staff). This catches Shearwater's own team *and* the Omni team without needing either hardcoded — anyone who shows up across many different clients' channels gets filtered the same way.
- **Internal back-channel is Engagement-scoped, like the client-facing channel** — not Client-scoped — because there's no guarantee an internal discussion channel survives past the engagement that spawned it; there may or may not be a next engagement to inherit it.
- Reads are cached short-TTL and fetched async off the initial page render (never block a page load on a live Slack call), matching the frontend's async-off-page-load pattern.

**Writes — one delivery mechanism, always in-app, regardless of LLM opt-in.** A single compose-box UI schedules a message with a short undo window (message composed and finalized *before* scheduling; the delay is purely an undo buffer, never a writing deadline). Whether the compose box opens **pre-filled** (from an approved `generated_content` post-call draft) or **blank** (typed fresh) is the *only* thing the LLM opt-in setting changes — the delivery mechanism itself is identical either way, and is the **recommended default path for every user**, not merely available to opted-out ones. Reasoning: leaving the app to post directly in Slack risks the CSM getting pulled into unrelated unread-notification threads and never actually sending the follow-up (or sending it hours late) — staying on-platform is a real workflow-efficiency win, not just a nice-to-have. Nothing prevents posting directly in Slack instead, but the product actively steers toward the in-app path.

- **Reads and writes fail asymmetrically, on purpose.** A dead/expired token degrades a *read* silently (don't let a Slack outage sink other features) but must surface loudly and redirect to reconnect on a *write* — a client-facing message must never silently fail to send.
- **Scope drift needs a reconnect path, not a raw error.** Different features need different OAuth scopes (channel reads, DM reads, name/email resolution, writes); a token created before a scope was added can't silently do the new thing — `missing_scope` routes to reconnect.

### Events — the spine

```sql
CREATE TABLE events (
  id BIGSERIAL PRIMARY KEY,
  occurred_at TIMESTAMPTZ DEFAULT now(),
  entity_type TEXT, entity_id UUID,
  event_type TEXT,                     -- 'stage_changed' | 'meeting_ingested' | 'deliverable_completed' | 'content_generated' | ...
  actor_user_id UUID REFERENCES users(id),
  payload JSONB
  -- append-only: no UPDATE, no DELETE
);
```
Write-path rule: every mutating service function appends the corresponding event in the same transaction, through one shared helper. Derived effects (health recompute, digest bucketing, checklist seeding, notifications) are handlers subscribed by `event_type`, never direct cross-service calls.

### Connections, time tracking, LLM usage

```sql
CREATE TABLE connections (
  user_id UUID REFERENCES users(id), provider TEXT,
  access_token TEXT, refresh_token TEXT, expires_at TIMESTAMPTZ, scopes TEXT[], extra JSONB,
  PRIMARY KEY (user_id, provider)
);

CREATE TABLE time_entries (
  id UUID PRIMARY KEY,
  engagement_id UUID REFERENCES engagements(id),
  user_id UUID REFERENCES users(id),
  harvest_id TEXT UNIQUE,           -- Harvest's own time-entry id (nullable: NULL until actually posted — see blocked_reason)
  external_ref_id TEXT,             -- the calendar event id stamped via Harvest's external_reference — our strong dedupe key
  spent_date DATE NOT NULL,
  hours NUMERIC NOT NULL,
  notes TEXT,
  source TEXT DEFAULT 'auto',       -- 'auto' | 'manual'
  status TEXT DEFAULT 'active',     -- 'active' | 'cancelled_but_logged' (ghost row — hours were billed, stays visible)
  needs_review BOOLEAN DEFAULT false,   -- set when task resolution fell through to the global default task
  blocked_reason TEXT               -- e.g. 'no_harvest_project' — fully computed entry, just not yet postable
);

CREATE TABLE llm_usage (
  id UUID PRIMARY KEY, user_id UUID REFERENCES users(id), feature TEXT, model TEXT,
  input_tokens INT, output_tokens INT, cost_usd NUMERIC, called_at TIMESTAMPTZ DEFAULT now()
);
```

**Harvest service-layer logic** (not schema, but load-bearing):
- Dedupe: strong key `(external_ref_id, spent_date)`; fallback `(spent_date, notes, hours)` only for rows with no `external_ref_id` (hours **must** be in the key — twin same-day same-title short calls bug); renamed-recurring-event healing only under strict 1:1/same-engagement/same-hours guard, otherwise soft-warn, never auto-guess; never post blind — abort the write if the pre-post dedupe fetch fails.
- Task resolution: single-task project → auto; multi-task → keyword-routing config, then 90-day usage-history fallback, then **global default fallback task** (e.g. "Business Development") with `needs_review=true` — task ambiguity is cheap to fix after the fact, so never block on it.
- Project resolution: **no fallback** — if `engagements.harvest_project_id` is unset, entries are computed and stored fully (`blocked_reason='no_harvest_project'`, `harvest_id` stays NULL) but deliberately **not posted**. This is intentional: it surfaces a real setup gap (Ops hasn't created the Harvest project yet) that people should be made aware of ASAP, not silently routed around. When `engagements.harvest_project_id` is later set, that update emits an event whose handler sweeps and posts every blocked entry for that engagement — backfill is automatic, not manual.
- Holdbacks: skip OOO/PTO/Focus/cancelled/declined outright; hold tentative/un-accepted invites out of the default "log all" batch (per-event override available); anchor day/week windows to a configured local timezone; every Harvest read defaults to `scope="me"`.

### Generated (LLM) content — cache pattern + opt-in

```sql
CREATE TABLE generated_content_kinds (
  kind TEXT PRIMARY KEY    -- 'post_call_draft' | 'pre_call_briefing' | 'client_snapshot' | 'weekly_digest' | 'csat_pulse'
);

CREATE TABLE generated_content (
  id UUID PRIMARY KEY,
  entity_type TEXT NOT NULL,    -- 'meeting' | 'engagement' | 'client' | 'user'
  entity_id UUID NOT NULL,
  kind TEXT REFERENCES generated_content_kinds(kind),
  payload JSONB NOT NULL,       -- shape varies entirely by kind
  model_used TEXT,
  generated_by_user_id UUID REFERENCES users(id),
  generated_at TIMESTAMPTZ DEFAULT now(),
  finalized_payload JSONB,      -- the human-edited version actually sent, if any (feeds digest few-shot self-tuning)
  sent_at TIMESTAMPTZ,          -- populated only when actually delivered
  input_hash TEXT               -- generation skipped if unchanged since the last run over the same inputs
);

CREATE TABLE llm_content_preferences (
  scope_type TEXT NOT NULL,     -- 'user' | 'engagement'
  scope_id UUID NOT NULL,       -- users.id or engagements.id
  kind TEXT REFERENCES generated_content_kinds(kind),
  enabled BOOLEAN NOT NULL,
  PRIMARY KEY (scope_type, scope_id, kind)
);
```

**Generation discipline** (load-bearing, ported almost verbatim from Hao's most expensive lesson):
- `GET` routes read the latest cached `generated_content` row for `(entity, kind)` only — never trigger generation, never block on an LLM call. No row → an honest "not generated" state.
- Generation only happens via an explicit `POST .../generate`, which writes a new row and emits a `content_generated` event.
- **`POST .../generate` itself checks for an existing non-stale row first** (via `input_hash` or a kind-specific staleness rule, e.g. the pre-call briefing's 6h window) and returns that instead of calling the LLM again — a separate "force regenerate" action is the only path that spends twice. This is also what makes cost sharing across viewers automatic: since most kinds are scoped to `entity_type` in {`meeting`,`engagement`,`client`} rather than `user` (see the entity-type table below), a second person opening the same meeting/engagement gets the first person's cached generation for free, not a second LLM call.
- **Opt-in gates generating, never reading.** `llm_content_preferences` decides whether *this actor* is allowed to trigger a new generation; it says nothing about whether they can view a row someone else (a teammate, or the rules engine) already produced and that they otherwise have visibility into. An admin/ops user with a kind turned off for themselves still sees an analyst's already-generated draft on an engagement they can see.
- The generation service function should assert/raise if invoked outside that explicit path — a runtime guard, not just a convention (Hao's team violated the convention-only version of this rule twice in production).
- Before generating, resolve opt-in: `engagement` row in `llm_content_preferences` for `(engagement_id, kind)` wins if present → else `user` row for `(user_id, kind)` → else **disabled**. No preference rows at all = everything off by default for a new user (the safe default doubles as the spend-control lever).
- Draft shape (technical/enablement/alignment/general) keyed off `meetings.session_type` stays as **app-level config**, not a DB table — static product vocabulary, no per-tenant customization need (unlike `stage_vocab`/`deliverable_kinds`, which do need DB-level flexibility since different engagement *types* need different values).
- Transcript is ground truth, summary is orientation-only fallback while transcript backfills (summaries measurably drop commitments — Hao's own 10-call validation found ~37 dropped across 10 calls).

**Status model** (replaces a flawed single linear "status ladder" from an earlier pass in this design — see §6 for why):
- **Call completion** (universal, independent of any LLM opt-in): `meetings.handled_at IS NULL` (open) vs. not (handled). This is the only thing every user needs, regardless of whether they use AI drafting at all.
- **Draft status** (conditional — only meaningful/shown when the opt-in resolves true for that user+engagement+kind): `none → awaiting_fathom → drafted → approved`, derived from transcript presence + `generated_content` row existence + an approval marker. Never conflated with call completion.
- "Approve" (finalize draft, write typed action items, trigger delivery) only exists when a draft exists. A **separate, LLM-free "mark handled" path** must fully exist for opted-out engagements — can still manually write `action_items` and send a manual note, none of it touching `generated_content`.

### Digests & CSAT pulses

Three more `generated_content_kinds`: `internal_digest`, `weekly_digest`, `csat_pulse` — all three opt-in through the **exact same, uniform** `llm_content_preferences` cascade as drafts/briefings (engagement override → user default → off). An earlier pass considered exempting CSAT from personal opt-out as an org-mandated process; rejected (see §6) — whether a given engagement's client wants to be surveyed is an analyst+Ops judgment call, not a platform-enforced default, and forcing it on would generate noise with little value for clients who aren't inclined to answer.

Worth being explicit about which kinds are shared vs. inherently personal, since it's easy to conflate: `post_call_draft` / `pre_call_briefing` (`entity_type='meeting'`), `client_snapshot` (`entity_type='client'`), and `csat_pulse` (`entity_type='engagement'`) all answer a question that's objectively about the meeting/client/engagement, not about who's asking — one generated row correctly serves an analyst, ops, and admin alike, all reading the same cache. `internal_digest` / `weekly_digest` (`entity_type='user'`) are the one genuine exception: whose portfolio, in whose voice via few-shot tuning, so two different users' digests are legitimately different documents even when both mention the same engagement — not an oversight, just a real difference in what the content *is*.

- **Internal digest** (Friday, `entity_type='user'`): scoped to the analyst's own owned Engagements. Bucketing is **live**, never a stale persisted signal — terminal engagements excluded entirely, Wrap-Up gets its own bucket, everything else buckets by current health. Delivered via the Slack compose→schedule→undo mechanism from the Slack integration section.
- **Weekly digest** (Monday, `entity_type='user'`): movers / drifting (with why) / concrete priorities / watch-outs, drawing on multi-channel recency (not call-only). No delivery mechanism — it's a page, not something sent anywhere.
- **Few-shot self-tuning** (both digests): generation pulls the analyst's last ≤2 rows with `sent_at IS NOT NULL` for that `(user, kind)` as style examples, so the tool learns their actual edited voice instead of needing hand-tuned prompt style rules.
- **CSAT pulse** (`entity_type='engagement'`): triggers on milestones (kickoff checklist complete, hours burn crosses 50%, stage → Wrap-Up — trigger vocabulary stays app-level config; no `cert_done` trigger — the Cert module is out of MVP scope, see §4). Delivered as a plain **Gmail draft**, not Slack — deliberately more conservative than the Slack mechanism (no scheduled auto-send/undo-window at all, since asking a client directly for feedback is more sensitive than a call recap; a human must open Gmail and send it themselves). Needs Gmail added as a fifth OAuth provider in `connections` (`gmail.compose` scope only, draft-only, mirroring the reference tool's never-auto-send rule).

```sql
CREATE TABLE csat_triggers_fired (      -- once-only firing guard, independent of the opt-in question above
  engagement_id UUID REFERENCES engagements(id),
  trigger_key TEXT NOT NULL,            -- app config vocab: 'kickoff_complete' | 'hours_burn_50' | 'stage_wrapup' | ...
  fired_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (engagement_id, trigger_key)
);
```

### Ask — the agentic assistant

In-process ReAct loop, deliberately **not** exposed as an MCP server — keeps per-user auth, budget, and (most importantly) the *structural* propose→confirm gate on writes enforceable server-side rather than trusted to the model. **Conversations are portfolio-wide** (not bound to one engagement at creation): the dominant use case is cross-engagement ad hoc questions ("who was Jack again?", "what's still open across my book?"), which demands the assistant be able to search and resolve across everything the user can see, not one engagement at a time.

```sql
CREATE TABLE chat_conversations (
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users(id),
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE chat_messages (
  id UUID PRIMARY KEY,
  conversation_id UUID REFERENCES chat_conversations(id),
  role TEXT NOT NULL,               -- 'user' | 'assistant'
  content TEXT,
  proposal JSONB,                   -- present only on a turn proposing a write: {action, resolved_entity_type, resolved_entity_id, params}
  proposal_status TEXT,             -- 'pending' | 'confirmed' | 'executed' | 'failed' | 'discarded'
  observation JSONB,                -- truncated tool-read result, if this turn was a read
  created_at TIMESTAMPTZ DEFAULT now()
);
```

**The core safety property**: each assistant turn is one strictly-validated JSON dispatch (`reply` / `read` / `action`), capped at a couple of rounds per turn; a malformed or unrecognized dispatch degrades to a plain reply and can structurally never fall through to a write. For a proposed `action`, the model references entities the way a person would (by name/description); a **server-side resolver** — scoped through the same `engagement_memberships` visibility as the rest of the app, no elevated access just for being a chat interface — turns that into a real, ownership-checked id *before* the confirm card is shown. An ambiguous reference returns a clarification question, never a guess (same "refuse to guess" principle as meeting matching). Confirm re-validates both `proposal_status='pending'` (blocks a double-execution race) and that the resolved entity is still visible to the user at confirm time, not just propose time. Execution runs through the normal service function — same event emission as any other write — so Ask bypasses none of the platform's usual discipline. A failed execute stays `pending` for retry; editing and re-asking discards the stale proposal branch but never un-executes something already confirmed.

**Reads**: a live portfolio digest reusing the *exact same* service function the board/Home surfaces call (never a separate computation, so Ask and the UI can't disagree about a client's health or open items); a **contact lookup** across every client the user can see (search `client_contacts` by name — the "who was Jack again?" case specifically needs this, not just the digest); and doc search over `doc_chunks` (below).

**Ask does not use `llm_content_preferences`** — that opt-in system exists to gate *background* spend a user didn't directly trigger; opening a chat and typing a question is already about as directly-chosen as spend gets. Cost control here is the loop cap and the standard per-feature budget/timeout table, not a separate toggle.

### Knowledge base (doc search)

```sql
CREATE TABLE doc_chunks (
  id UUID PRIMARY KEY,
  source_prefix TEXT NOT NULL,    -- 'web' | 'internal'
  source_id TEXT,                 -- Notion page id for 'internal'; a URL slug for 'web'
  title TEXT,
  url TEXT,                       -- ONLY ever populated for source_prefix='web' — see below
  content TEXT NOT NULL,          -- chunked, full-text indexed (Postgres tsvector)
  updated_at TIMESTAMPTZ DEFAULT now()
);
```

Two sources, two ingestion mechanisms, one table:
- **`web`**: public Omni vendor documentation (`docs.omni.co`) — a weekly cron does one GET of Omni's published docs feed, replace-on-success. Kept as-is; still relevant since Shearwater is Omni-focused.
- **`internal`**: the company's internal playbook, which currently lives in Notion — a scheduled sync job (reusing the existing job-queue infrastructure, not a new mechanism) pulls specified pages, chunks them, and upserts into `doc_chunks` keyed by Notion page id (idempotent re-sync). Ships as working plumbing now; populated once actual page ids are pointed at it. Treated as readable by all internal staff with no extra ACL for now — worth a finer-grained rule later only if some playbook content turns out to need restricting.

**`url` is only ever populated for `web` rows** — this is what makes "internal content never leaks into a client-facing link" a structural property rather than a convention someone has to remember: draft-generation has nothing to cite from an internal row even if it matched a search, because there's no URL there to cite.

**A third source (`cert`) was deliberately never added** — the Cert/training module is out of MVP scope entirely (§4), not merely deferred pending a decision.

### Client-facing portal

Builds directly on the deferred external auth (§2 row 16) and `engagement_memberships` viewer grants. Turned out to be more than a read-only status page once discussed through — the real value is visibility **and** async collaboration (comments + client review verdicts), not just a dashboard.

**Authentication — invite-only, not self-serve.** Unlike internal Google OAuth (open to anyone with a valid `@shearwaterdata.com` address), a `client_external` account must be created first by an analyst/ops action (inviting a specific `client_contacts` email to view a specific engagement) — only then can that email request a login link.

```sql
CREATE TABLE magic_link_tokens (
  token TEXT PRIMARY KEY,          -- random, high-entropy
  user_id UUID REFERENCES users(id),
  expires_at TIMESTAMPTZ NOT NULL,
  used_at TIMESTAMPTZ,             -- single-use: NULL until consumed
  created_at TIMESTAMPTZ DEFAULT now()
);
```

Flow: request → the app responds identically ("if that email has access, a link has been sent") whether or not the email actually has an account, to avoid a user-enumeration side channel → emailed link → click → validate unused+unexpired → session cookie, same session mechanism internal users get. Revocation is just deleting the `engagement_memberships` row; the `client_external` user account persists (same as an internal analyst losing ownership doesn't delete their account). Invite action is gated the same way other engagement-scoped actions are: owner/collaborator on that engagement, or ops/admin.

**What's visible — Deliverables only, deliberately narrow.** Engagement name/stage, and its deliverables (name, kind, `pipeline_status`, blocked/blocked_reason, target_date, rolled-up progress). Not shown: `deliverable_activity` (internal-only, see below), meetings, action items, health, hours/burn, or the contact roster — health specifically because it's an internal risk signal, not something to editorialize to a client about their own relationship. No granular per-field visibility-toggle system (the reference tool's `client_portal_visibility`) — that's speculative flexibility for later, once there's real demand for exposing something beyond this.

**Two comms channels, structurally separate, not one table with a flag.** `deliverable_activity` stays fully internal (status-change log + internal commentary) — a client never sees it. A genuinely new, bidirectional channel exists for client-facing comms:

```sql
CREATE TABLE deliverable_comments (
  id UUID PRIMARY KEY, deliverable_id UUID REFERENCES deliverables(id),
  author_user_id UUID REFERENCES users(id),   -- internal or client_external
  body TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT now()
);
```

Two tables rather than a visibility flag on one is a deliberate choice: a flag is one mis-set value away from leaking internal chatter to a client; a separate table makes that structurally impossible, same principle as `doc_chunks.url` only ever being populated for `web` rows. Anyone who can see a deliverable can read and post into its comment thread — commenting is low-stakes enough not to need tighter gating than visibility itself.

**Client review verdicts — a UAT-style signal, layered on top of the pipeline, never auto-mutating it.**

```sql
CREATE TABLE deliverable_client_reviews (
  id UUID PRIMARY KEY, deliverable_id UUID REFERENCES deliverables(id),
  reviewer_user_id UUID REFERENCES users(id),   -- a client_external user
  verdict TEXT NOT NULL,   -- 'accepted' | 'blocked' | 'rejected'
  created_at TIMESTAMPTZ DEFAULT now()
);
```

Kept as a history (UAT is realistically iterative — reject, fix, resubmit, re-review) rather than one overwritable column; current verdict = the latest row, derived at read time. **A client's "accepted" verdict never auto-transitions `pipeline_status` to done** — it surfaces as an actionable signal the assigned analyst still has to act on, same "completion is always an explicit human decision" principle already applied to stage/health elsewhere. Flagged as a natural first case for the still-undesigned Rules engine (notify the assignee the moment a review lands), alongside the Harvest blocked-hours case already noted there.

**Per-client-user deliverable scoping — the client's own org structure, not ours.** A project lead should see everything; their own QA/analyst team should see only what they specifically own. This layers onto the existing engagement-level grant rather than needing a separate access system:

```sql
ALTER TABLE engagement_memberships ADD COLUMN viewer_scope TEXT DEFAULT 'full';   -- 'full' | 'assigned_only'
```

Visibility for an `assigned_only` viewer:
```
visible(deliverable, viewer) = viewer_scope = 'full'
  OR deliverable.client_owner_user_id = viewer.id
  OR deliverable has a descendant where client_owner_user_id = viewer.id   -- see the parent for context; tree is ≤2 levels, so this is a single parent-check, not real recursion
```

The same predicate gates `deliverable_comments` and `deliverable_client_reviews`, not just the deliverable list. It also gates **who can submit a review verdict**: a `full`-scope project lead can review/flag anything they can see (oversight authority); an `assigned_only` client user can only submit a verdict on deliverables where they're the designated `client_owner_user_id`.

This is what drove splitting `deliverables.assignee_user_id` into two independent columns (see §3 Deliverables) — one internal-facing (who's doing the work), one client-facing (who owns validation/sign-off) — since the same deliverable routinely has both, from two different organizations, and a single column couldn't represent that.

### Onboarding

Simpler than the reference tool's 4-step wizard, because two of its steps no longer apply: **no "connect LLM" step** (the org-level key means nothing personal to authorize), and **no "confirm discovered clients" step** (discovery is deferred entirely — a first sync just matches meetings against whatever the analyst already has `engagement_memberships` access to; anything unmatched lands in the same unassigned-meetings backfill screen a returning user would hit, not a special onboarding-only surface).

**No hard finish gate.** The reference tool required Fathom + LLM connected before finishing, because its entire data model *was* the sync pipeline. Ours doesn't work that way — manual Client/Engagement/Deliverable creation is a fully standalone path needing zero integrations. So every connection is recommended, not required: a persistent, dismissible "finish setup" nudge rather than a blocking modal, computed live from what's actually in `connections` rather than tracked as separate wizard state.

```sql
ALTER TABLE users ADD COLUMN onboarded_at TIMESTAMPTZ;              -- stamped on first sync (wizard or manual) — switches deep-first-sweep to incremental lookback
ALTER TABLE users ADD COLUMN onboarding_banner_dismissed BOOLEAN DEFAULT false;
```

The wizard itself: **profile** (display name, timezone — anchors Harvest day windows) → **connect** (Calendar+Fathom first since they drive meeting ingest, then Harvest, then Slack, then Gmail last since it's only used for the already-opt-in CSAT pulse; the OAuth `return_to` round-trip pattern from the reference tool is worth keeping so a connect started mid-wizard lands back on the same step, validated as a same-origin relative path to avoid becoming an open-redirect) → **first sync** (deep-first-sweep 90-day window, same as before) → **LLM preferences nudge** (a skippable pointer at `llm_content_preferences`, since everything defaults off and a new analyst might not otherwise find the settings page).

**Explicitly out of scope for the wizard**: granting `engagement_memberships` (deciding which clients/engagements a new analyst can see) is business knowledge the system can't infer — an ops-side action taken as part of hiring/assignment, entirely separate from the new analyst's own account setup.

### Rules engine

Almost no new storage needed — it's built almost entirely on top of the event bus and `generated_content`, which is a nice validation of both.

```sql
CREATE TABLE rules (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  trigger_type TEXT NOT NULL,       -- 'no_touchpoint' | 'stage_age' | 'action_overdue' | 'hours_burn' | 'cancel_pattern' | 'checklist_overdue' | 'compound' | 'sessions_not_scheduled' | 'on_event'
  trigger_config JSONB NOT NULL,    -- thresholds, event_type filter, compound sub-rule refs, etc.
  action_type TEXT NOT NULL,        -- 'raise_alert' | 'draft_nudge'
  action_config JSONB,
  engagement_type_key TEXT REFERENCES engagement_types(key),   -- NULL = every type; set = type-specific (generalizes the old qs_invoice_readiness-style gate)
  enabled BOOLEAN DEFAULT true,
  created_by_user_id UUID REFERENCES users(id),
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE rule_firings (            -- cooldown tracking only — one row per (rule, engagement), not per viewer
  id UUID PRIMARY KEY, rule_id UUID REFERENCES rules(id), engagement_id UUID REFERENCES engagements(id),
  fired_at TIMESTAMPTZ DEFAULT now(), cooldown_until TIMESTAMPTZ
);
```

**Definitions are a shared, admin-editable playbook; execution and cooldown are per-Engagement, never per-viewer.** A rule evaluates against one engagement's state once per cycle regardless of how many people (analyst, ops, admin) can see that engagement — visibility of the result is computed separately, at read time, via the usual `engagement_memberships`/role-bypass rules. More viewers costs nothing extra on the evaluation side.

- **`stage_age`** needs no new timestamp column — it's "days since the last `stage_changed` event for this engagement," read straight from `events`. The event bus doing real work as a spine, not just an audit trail.
- **`no_touchpoint` / `hours_burn` / `checklist_overdue`** reuse `engagement_signals`, `time_entries`, and `deliverables.pipeline_status` — no new inputs.
- **`on_event`** rules don't get polled at all — they register as ordinary event-bus handlers, reacting immediately. Everything else runs off a scheduled job (the existing job queue), checking `rule_firings.cooldown_until` before re-evaluating.
- **`raise_alert`** is just another `events` row (`event_type='rule_alert_raised'`, payload carries the message) — a dashboard is nothing more than "recent alert events for engagements I can see." No dedicated alert table. A "mark at risk" severity is a tag on this event's payload, **never a write to any health-adjacent column** — health stays fully derived, a rule can flag something loudly but can't hand-author the number itself.
- **`draft_nudge`** is just another `generated_content_kinds` entry (`entity_type='engagement'`), gated through the same `llm_content_preferences` cascade as everything else — except a rules-engine firing has no acting user to resolve opt-in against. **Resolved against the preference of the user holding the engagement's `owner` membership** (there's no owner column — see §6) — they're the one who'd actually act on or send the nudge, so it's their opt-in that gates whether the system spends on their behalf. An engagement with no owner membership resolves to disabled, same as a missing preference row.
- **NL→YAML rule authoring** (an admin describes a rule in plain language, gets a schema-constrained draft) is an ungated admin utility, not routed through `llm_content_preferences` — rare, admin-only, not recurring per-engagement generation.

### Templates/checklist engine

Collapses to mostly just the generalized Deliverables engine again. Two of the reference tool's three template concerns don't apply here: **portal visibility seeding** is moot (the granular per-field toggle system was deferred entirely — portal is Deliverables-only); **default rules seeding** is moot (rules aren't instantiated per-engagement in our design, they're globally defined and `engagement_type_key`-scoped, applying automatically). What's left is checklist seeding, and a checklist item is structurally just a lightweight Deliverable:

```sql
CREATE TABLE deliverable_templates (
  id UUID PRIMARY KEY,
  engagement_type_key TEXT REFERENCES engagement_types(key),
  trigger_event TEXT NOT NULL,     -- e.g. 'engagement_created' | 'stage_entered:kickoff' — app config vocab
  items JSONB NOT NULL,            -- [{kind, name, priority, ...}] — the seed list
  created_at TIMESTAMPTZ DEFAULT now()
);
```

Instantiation is another event-bus handler subscribed to `trigger_event` — creates the listed deliverables the first time the trigger fires for an engagement, idempotent (never re-creates items matched by existing kind+name). A template edited later (SOP evolves, a new item gets added) doesn't retroactively touch engagements automatically — a manual "sync to latest template" action lets an analyst/ops pull in just the new items, additive-only, never disturbing progress already made on existing ones.

### Testing strategy

The reference tool's one-invariant-per-file convention (filename = the pinned contract) carries over wholesale — stack-independent, genuinely good practice. What's new or changed for this design specifically:

- **Two populations now need scope-isolation tests, not one.** The reference tool only ever isolated internal analysts from each other. We also have `client_external` viewers with `viewer_scope='assigned_only'`, so every deliverable-touching feature needs both a `*_engagement_scope_isolation` test (an analyst without membership can't see/act on it) *and* a `*_portal_scope_isolation` test (a scoped client viewer can't see past their `client_owner_user_id`) — genuinely new territory, not something the old pattern already covers.
- **A generation-cache test**: a second `POST .../generate` for the same entity+kind must return the cached row without a second LLM call (monkeypatched call-count assertion) — guards the cost-sharing mechanism from §3/§6 directly.
- **An event-emission drift guard**: every mutating service function asserted to emit its corresponding `events` row — catches a future write path that isn't wired into the spine.
- **Elevated stakes on XSS/sanitization tests**: bidirectional `deliverable_comments` means untrusted content now flows across the trust boundary in both directions (client → internal, internal → client), not one — treat as higher-priority than the reference tool's version, not a like-for-like port.
- **Harvest dedupe tiers and meeting-matching precedence** port as direct regression tests — each one is a "paid for this bug once" incident, stack-independent.
- **DB testing shape changes with Postgres**: no more SQLite temp-file-per-test — a shared test Postgres instance with each test wrapped in a rolled-back transaction is the standard replacement.

---

## 4. Explicitly deferred scope (real decisions, not gaps)

| Item | Reasoning | Revisit when |
|---|---|---|
| Auto-discovery of new Clients/Engagements from sync (confirm/exclude queue) | Manual creation + the unassigned-meetings backfill screen cover v1 needs without the added complexity of a confirm/candidate lifecycle. | Friction of manual entry is actually felt at scale. |
| BigQuery / Omni analytics integration | Wrong tool for the OLTP write path; a downstream sink fed from Postgres is the right shape, not needed for v1. | Cross-org/leadership reporting needs grow past what the app's own views cover. |
| Per-user/per-workspace Claude API keys | Ops/Finance haven't decided how they want AI spend billed; a single org key + per-user `llm_usage` log keeps the decision reversible without any rework. | Finance wants true chargeback, or per-team budget ceilings. |
| Managed auth-as-a-service (e.g. WorkOS) | Hand-rolled Google OAuth is faster to ship for the internal-only MVP. | If/when the external-auth build turns out heavier than expected, worth revisiting. |
| Granular per-field portal visibility toggles (à la the reference tool's `client_portal_visibility`) | Portal ships Deliverables-only for now; that's speculative flexibility with no demand behind it yet. | A specific engagement needs to expose something beyond deliverables. |
| Cert/training module | Explicitly cut from MVP scope by Erwin — not merely low-priority, a decided no. | Not currently expected to revisit; would need a fresh ask if training/certification becomes a real product need. |

---

## 5. Open — not yet designed

Every v1 design item was settled as of 2026-09-24. This section holds whatever surfaces once development starts (a real design question always turns up mid-build that this document didn't anticipate). When that happens: add it here, work through it the same way as everything above, fold the resolution into §2/§3, and log the reasoning in §6 — the same loop this whole document was built from.

**None open.** The six items surfaced during the PoC build (2026-09-24) were all resolved the same day and folded into §2/§3 — see §6.

---

## 6. Decision log

Dated entries for traceability — why something is the way it is, in case it's questioned later.

- **Client/Engagement split**: Meetings, Slack channel, and Harvest project mapping are Engagement-scoped by default (not Client-scoped) — explicit correction from an earlier draft that had them on Client. Reasoning: no guarantee of the same Slack channel across engagements months apart with the same client; keeping engagement views uncontaminated by other engagements' history was an explicit UX goal. Contacts stayed Client-scoped as the one deliberate exception (low UX cost, real continuity value).
- **Deliverables vs. action items**: kept as two separate stores after clarifying that "deliverable" means planned/scoped work product (pipeline+blockers) while "action item" means an ad hoc conversational commitment extracted from a call (flat open/done/dismissed) — collapsing them would repeat the "one surface answers one question" lesson violation from Hao's tool.
- **Discovery/confirm queue**: originally designed (generalizing Hao's `client_confirmed`), then deferred entirely once re-explained from first principles and judged not worth the added complexity for v1 — replaced by manual creation + the unassigned-meetings backfill mechanism, at Erwin's explicit request to make sure backfill stays easy.
- **Claude billing**: single org-level key chosen over per-user keys/subscriptions specifically because Ops/Finance preference is unknown and provisioning per-user keys was judged likely messy — kept reversible via the `llm_usage` log.
- **Harvest task resolution default**: changed from "block/unresolved" to "log against a global default task, flag `needs_review`" at Erwin's explicit pushback — task-level mistakes are cheap to fix after the fact, unlike dedupe mistakes, so "refuse to guess" was overkill here.
- **Harvest project resolution**: deliberately kept as a hard block (no fallback project) at Erwin's explicit request — an engagement missing its Harvest project mapping is a real Ops process gap (they haven't created the project yet) that should surface loudly, not be silently routed around. Entries are still fully computed and stored (`blocked_reason`), so backfill is automatic once Ops creates the project.
- **Frontend**: server-rendered + htmx chosen over a SPA after an explicit tradeoffs discussion (feature limitations, UI customization, responsiveness, reversibility) — none of the app's actual planned features need a component framework, and the single-codebase/no-build-pipeline simplicity matches every other choice in this design; kept reversible per-page as long as business logic stays decoupled from rendering.
- **Status ladder redesign**: the original single linear ladder (`handled → approved → drafted → awaiting_fathom → ready`) was flawed — it implicitly assumed LLM drafting as the default path for every call. Once drafting became opt-in (per Erwin: he doesn't use it personally, others depend on it heavily — "a great lever for efficient spending"), this was split into two independent axes: universal call-completion status, and a conditional draft-status sub-state that only exists when the opt-in resolves true. Opt-in itself was then made per-kind (not one coarse flag) at Erwin's request, since he wants post-call drafting on but pre-call briefings off personally — `llm_content_preferences` replaced the earlier flat boolean columns on `users`/`engagements`.
- **Slack write delivery is always in-app, not conditional on LLM opt-in**: an earlier pass framed the compose→schedule→undo mechanism as "available but optional" for opted-out users, implying they'd just post in Slack directly. Erwin pushed back — staying on-platform avoids the real risk of a CSM opening Slack, getting pulled into unrelated unread threads, and delaying or forgetting the follow-up entirely. The mechanism is now the recommended default for everyone; LLM opt-in only decides whether the compose box starts pre-filled or blank, not whether the in-app path is used at all.
- **Internal Slack back-channel scoped to Engagement, not Client**: at Erwin's explicit reasoning — an internal discussion channel has no guaranteed continuity across engagement boundaries (a channel from a closed engagement may or may not carry into a future one with the same client), so it follows the same ephemeral-by-default rule as the client-facing channel and Harvest project mapping.
- **Slack health-signal value questioned, then kept**: Erwin wasn't sure how useful multi-channel Slack-derived health really is, but agreed it's worth keeping since it's computed with zero LLM cost — real value specifically for engagement types with significant between-call Slack activity (retainers, ongoing partnerships), smaller for short call-driven engagements, essentially free either way.
- **CSAT pulse opt-in stays personal, not org-mandated**: an initial proposal exempted CSAT from the per-user `llm_content_preferences` toggle (via `generated_content_kinds.default_enabled`/`allow_user_override` flags) on the theory that client feedback collection is a company policy decision, not personal productivity. Erwin rejected this — whether a specific client is inclined to answer a survey is an analyst+Ops judgment call, not the platform's to force, and mandating it would generate noise for little value in cases where it's unneeded. CSAT resolves through the exact same opt-in cascade as every other `generated_content` kind; the proposed extra columns were dropped as unneeded complexity.
- **Doc search corpus decided source-by-source, not as one blanket call**: the reference tool blended three sources (public Omni vendor docs, an internal playbook, cert/training material) into one index. Broken apart on inspection: `web` (Omni docs) kept as-is since Shearwater is still Omni-focused; `internal` (playbook) gets the structure built now even without content, since the actual pages exist in the company's Notion workspace and just need pointing at; `cert` dropped entirely as a direct consequence of cutting the Cert module from MVP scope (a decided no, not a deferred maybe).
- **Cert/training module cut from MVP entirely**: previously an open "needs an explicit yes/no" item; Erwin decided no. Moved from Open (§5) to Deferred (§4) as a real decision, not left ambiguous.
- **Notion access is org-level, not per-user**: unlike every other integration (Calendar/Fathom/Harvest/Slack/Gmail, all per-user OAuth), the internal playbook lives in one shared company Notion workspace — a single admin-configured token is the right shape, mirroring the single-org-key reasoning already used for Claude billing.
- **Portal scope expanded well beyond read-only status once discussed through**: an initial pass scoped the portal to Deliverables-only, no comments, no client-side write capability at all. Erwin pushed for two-way async comms (a client-facing comment thread) and a UAT-style review/flag capability (Accepted/Blocked/Rejected) as the real "killer feature," not an afterthought — the portal's value is visibility *and* collaboration. Comments live in a structurally separate table from internal activity (never a visibility flag on one shared table), and review verdicts never auto-mutate `pipeline_status` — same "explicit human decision" principle already applied to health/stage.
- **`deliverables.assignee_user_id` split into two columns**: `internal_assignee_user_id` (who's doing the work) and `client_owner_user_id` (who owns client-side validation/sign-off) are independent and routinely both populated for the same deliverable — one column couldn't represent a dev/analyst and a client QA owner simultaneously. This also became the key for per-client-user portal visibility scoping (`engagement_memberships.viewer_scope`): a project lead sees everything in an engagement, while a client-side QA/analyst user sees only deliverables where they're the `client_owner_user_id` (plus ancestors, for context) — the client's own org structure, not something Shearwater imposes.
- **Onboarding has no hard finish gate**: the reference tool required Fathom + LLM connected to finish, because its data model *was* the sync pipeline. Ours isn't — manual creation works standalone, and there's no per-user LLM credential to require anymore anyway (org-level key). Confirmed explicitly by Erwin rather than assumed: every integration is a recommended, dismissible nudge, never a blocker.
- **`mark_at_risk` redesigned as an alert severity tag, not a health override**: the reference tool's rules engine could write directly to a manual health-override column; we don't have one, on purpose (health is fully derived). Confirmed with Erwin via a concrete walkthrough (an Acme Corp engagement seen by an Admin, an Ops user, and one assigned Analyst) that also surfaced two things worth stating explicitly rather than leaving implicit: (1) rules evaluate and cooldown per-Engagement, never per-viewer — visibility of the result is a separate, read-time concern; (2) most `generated_content` kinds are shared across every viewer who can see the entity by construction (`entity_type` in meeting/engagement/client), so a cache-check-before-generate rule in `POST .../generate` is what actually prevents three viewers from tripling LLM spend on the same meeting — only `internal_digest`/`weekly_digest` are genuinely personal and can't be shared. The walkthrough also exposed a real gap: a rules-engine-triggered `draft_nudge` has no acting user to resolve opt-in against, so it resolves against the engagement owner's preference instead.
- **Templates engine collapsed from three concerns to one**: the reference tool's template engine seeded checklists, portal visibility, and default rules. Two of those turned out to be moot here as a direct consequence of earlier decisions — portal visibility toggles were deferred entirely, and rules are globally-defined/type-scoped rather than per-engagement, so neither needs seeding. Only checklist seeding survives, and it reuses the Deliverables engine rather than inventing a second entity type, since a checklist item is structurally just a lightweight deliverable.
- **v1 design phase closed out (2026-09-24)**: every tracked Open item is resolved. §5 stays in the document as a live placeholder rather than being deleted, since a real design question always surfaces mid-build — the expectation going forward is that it gets added there, worked through the same way, and folded back into §2/§3/§6, not left to live only in a conversation or a commit message.
- **`quickstart` and `migration` stage/kind vocabularies drafted (2026-09-24)**: previously left as "shape defined, vocab TBD" in §3. Forced concrete by PoC seed-data needs; drafted from the reference tool's actual vocabulary (its QS stage list, its 6-stage migration skeleton including the semantic-layer-parity gate) rather than invented fresh — see engagement_types and deliverable_kinds seed statements above.
- **`engagements.owner_user_id` dropped — ownership lives only in `engagement_memberships` (2026-09-24)**: surfaced during the PoC, where the column and `role='owner'` memberships were two unreconciled sources of truth for the same fact. Erwin: the column was a legacy carryover from the reference tool. An engagement's owner is now its single `owner` membership (partial unique index enforces one); the rules engine's `draft_nudge` opt-in resolves against that membership instead of a column.
- **`dep_state` definitions confirmed (2026-09-24)**: §3 had named the four states without defining them. The PoC's definitions (blocked / maybe_unblocked / waiting / clear, built from the manual flag plus blocker edges, "finished" = done or N/A) were confirmed by Erwin as-is and folded into §3 Deliverables. The load-bearing choice is that `maybe_unblocked` never auto-clears the manual flag — the same "explicit human decision" principle as elsewhere.
- **Gate stages removed entirely (2026-09-24)**: the migration vocab drafted from the reference tool flagged `semantic_parity` as a `"gate": true` stage (don't start dashboards until the semantic layer matches the old tool). Erwin rejected the concept outright, not just its enforcement: in real projects modeling and dashboarding happen in tandem, and a gate reinforces the wrong expectation that one must wait for the other. The flag is gone from the vocab, the schema and the UI; this supersedes the "including the semantic-layer-parity gate" wording in the vocab-drafting entry above. Stages describe where an engagement is, never what work is allowed.
- **PoC provisional answers signed off (2026-09-24)**: the three remaining PoC-surfaced items were confirmed by Erwin as implemented. (1) `deliverable_kinds.parent_kind` is the mechanism for the ≤2-level trees (§3 Deliverables). (2) The permission model: ops/admin create clients/engagements and manage memberships; owner/collaborator (or ops/admin) edit deliverables and stages; viewer is read-only; invisible means 404 (§3 Identity & access). (3) The passcode-gated demo login stays as the way to demo seeded users until admin impersonation exists (§2 row 16). §5 is empty again.
