# Corporate CSM/Engagement Platform — Living Architecture Spec

Status: **design-in-progress**. This file is the source of truth for the rebuild being planned in conversation with Erwin (erwin@shearwaterdata.com). It supersedes anything said in chat — if chat and this file disagree, this file wins unless a chat message explicitly amends it (and this file should be updated in the same turn that happens).

**How to use this doc**: Section 2 is the settled foundation. Section 3 is the concrete schema — the actual source of truth for implementation. Section 4 is scope explicitly cut for v1 (not forgotten, just sequenced later). Section 5 is what's still undecided. Section 6 is a dated decision log for traceability ("why did we do it this way").

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
| 16 | **Authentication** | Hand-rolled (not a managed provider like WorkOS), chosen for MVP speed since internal users are the near-term priority and external users are out of scope for now anyway. **Internal** (`analyst`/`contractor`/`ops`/`admin`): Google OAuth restricted to `@shearwaterdata.com`, verified server-side (the `hd` hint is not enforcement). **External** (`client_external`): passwordless email magic-link owned entirely by the app — deferred until the client-portal work begins (see §4), since it's the same piece of work as that feature. First-login bootstrap via an `ADMIN_EMAILS` env var seeding initial `internal_role='admin'` rows (avoids a chicken-and-egg problem); everyone else defaults to `analyst`. Session = signed httponly secure cookie holding the user id. CSRF via origin/referer check on state-changing routes, same principle as Hao's tool. Worth carrying over conceptually (not yet built): Hao's actor-vs-scope split for admin "act as another user" impersonation with audit trail. |
| 17 | **OAuth/connections** (Calendar, Fathom, Harvest, Slack) | Per-user-per-provider token storage, same shape as Hao's `connections` table, FK'd to real `users.id` instead of email strings. |
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
```

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
  stage_vocab JSONB NOT NULL           -- ordered [{key,label}], drives board columns — vocab itself not yet drafted for any type
);

CREATE TABLE engagements (
  id UUID PRIMARY KEY,
  client_id UUID REFERENCES clients(id),
  type_key TEXT REFERENCES engagement_types(key),
  name TEXT NOT NULL,                  -- "Omni Migration — 2026" etc, since a client can have several over time
  stage TEXT NOT NULL,                 -- validated app-side against the type's stage_vocab
  status TEXT DEFAULT 'active',        -- active | paused | complete | cancelled
  owner_user_id UUID REFERENCES users(id),
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
);
```

> Note: `engagements.llm_content_override` as a flat boolean was likewise an intermediate design, **superseded** by `llm_content_preferences`. Do not implement the flat column.

### Deliverables — the generalized tracking engine

```sql
CREATE TABLE deliverable_kinds (
  engagement_type_key TEXT REFERENCES engagement_types(key),
  kind TEXT NOT NULL,
  display_name TEXT,
  PRIMARY KEY (engagement_type_key, kind)
);

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
  assignee_user_id UUID REFERENCES users(id),
  priority TEXT, target_date DATE,
  hours_estimated NUMERIC,
  created_at TIMESTAMPTZ DEFAULT now(),
  FOREIGN KEY (engagement_type_key, kind) REFERENCES deliverable_kinds(engagement_type_key, kind)
);

CREATE TABLE deliverable_blockers (
  blocked_id UUID REFERENCES deliverables(id),
  blocker_id UUID REFERENCES deliverables(id),
  PRIMARY KEY (blocked_id, blocker_id)
  -- app enforces: no self-loop, no cycle; dep_state (blocked/waiting/maybe_unblocked/clear) is derived, never stored
);

CREATE TABLE deliverable_activity (
  id UUID PRIMARY KEY, deliverable_id UUID REFERENCES deliverables(id),
  actor_user_id UUID REFERENCES users(id), kind TEXT, body TEXT, created_at TIMESTAMPTZ DEFAULT now()
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

- **Internal digest** (Friday, `entity_type='user'`): scoped to the analyst's own owned Engagements. Bucketing is **live**, never a stale persisted signal — terminal engagements excluded entirely, Wrap-Up gets its own bucket, everything else buckets by current health. Delivered via the Slack compose→schedule→undo mechanism from the Slack integration section.
- **Weekly digest** (Monday, `entity_type='user'`): movers / drifting (with why) / concrete priorities / watch-outs, drawing on multi-channel recency (not call-only). No delivery mechanism — it's a page, not something sent anywhere.
- **Few-shot self-tuning** (both digests): generation pulls the analyst's last ≤2 rows with `sent_at IS NOT NULL` for that `(user, kind)` as style examples, so the tool learns their actual edited voice instead of needing hand-tuned prompt style rules.
- **CSAT pulse** (`entity_type='engagement'`): triggers on milestones (kickoff checklist complete, hours burn crosses 50%, stage → Wrap-Up — trigger vocabulary stays app-level config; a `cert_done` trigger is deferred pending the Cert module decision). Delivered as a plain **Gmail draft**, not Slack — deliberately more conservative than the Slack mechanism (no scheduled auto-send/undo-window at all, since asking a client directly for feedback is more sensitive than a call recap; a human must open Gmail and send it themselves). Needs Gmail added as a fifth OAuth provider in `connections` (`gmail.compose` scope only, draft-only, mirroring the reference tool's never-auto-send rule).

```sql
CREATE TABLE csat_triggers_fired (      -- once-only firing guard, independent of the opt-in question above
  engagement_id UUID REFERENCES engagements(id),
  trigger_key TEXT NOT NULL,            -- app config vocab: 'kickoff_complete' | 'hours_burn_50' | 'stage_wrapup' | ...
  fired_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (engagement_id, trigger_key)
);
```

---

## 4. Explicitly deferred scope (real decisions, not gaps)

| Item | Reasoning | Revisit when |
|---|---|---|
| Auto-discovery of new Clients/Engagements from sync (confirm/exclude queue) | Manual creation + the unassigned-meetings backfill screen cover v1 needs without the added complexity of a confirm/candidate lifecycle. | Friction of manual entry is actually felt at scale. |
| BigQuery / Omni analytics integration | Wrong tool for the OLTP write path; a downstream sink fed from Postgres is the right shape, not needed for v1. | Cross-org/leadership reporting needs grow past what the app's own views cover. |
| Per-user/per-workspace Claude API keys | Ops/Finance haven't decided how they want AI spend billed; a single org key + per-user `llm_usage` log keeps the decision reversible without any rework. | Finance wants true chargeback, or per-team budget ceilings. |
| External (`client_external`) authentication / client-facing portal | Not the MVP's primary use case; internal analysts are the first test users. | Explicitly picked up as its own Open item (§5) — this is where the magic-link flow gets built. |
| Managed auth-as-a-service (e.g. WorkOS) | Hand-rolled Google OAuth is faster to ship for the internal-only MVP. | If/when the external-auth build turns out heavier than expected, worth revisiting. |

---

## 5. Open — not yet designed

In rough dependency order (most foundational/highest downstream impact first, per the ordering principle used so far):

1. **Ask / agentic assistant** — pattern worth keeping from Hao's tool: propose→confirm, server-side resolvers (never let the model invent an id), structural can't-reach-a-write degradation. **(Next up.)**
2. **Client-facing portal** — where the deferred external magic-link auth gets built; visibility already falls out of `engagement_memberships` viewer grants, so this is mostly UI + the auth flow.
3. **Onboarding flow** for a new analyst/contractor.
4. **Rules engine** (automated nudges — no_touchpoint, stage_age, hours_burn, etc.). The Harvest "unposted hours pending a missing project" surface (§3) is flagged as this engine's first concrete case once it exists.
5. **Cert/training module** — Hao's platform-map inventory says "probably leave behind" (Omni-specific content). Needs an explicit yes/no rather than a silent drop.
6. **Templates/checklist engine** (kickoff checklists, portal visibility templates).
7. **Testing strategy** — Hao's one-invariant-per-file pattern flagged as worth keeping conceptually; nothing concrete decided for the new stack yet.

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
