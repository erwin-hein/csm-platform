"""The design decisions behind the schema, written for the admin schema explorer (a PoC demo aid).

Everything structural on the explorer (tables, columns, keys, constraints, row counts) comes from
the live database, and the per-column comments from CLAUDE.md §3. These notes add the one thing
neither holds: *why*, in a sentence or two, pointing back at the CLAUDE.md §6 entry that records
the decision. Listed in the order a walkthrough would tell them.
"""

DECISIONS: list[dict] = [
    {"table": "clients", "title": "Client ≠ Engagement",
     "text": "A client is only a stable identity: name, domains, contacts. Everything time-bound (meetings, "
             "Slack channels, Harvest projects, deliverables) hangs off an engagement, so one client's thriving "
             "retainer and stalled migration never blur into one record.",
     "ref": "§2 row 1 · §6 Client/Engagement split"},
    {"table": "engagement_types", "title": "An engagement type is a contract",
     "text": "Each type declares its stage vocabulary and its deliverable kinds as data. A new type registers "
             "rows here; it never forks code. stage_kind says which deliverable stands for a stage (migration → "
             "phase), which is what lets stages be derived and run concurrently.",
     "ref": "§2 row 2 · §6 Stages derived and concurrent"},
    {"table": "deliverables", "title": "One tracking engine for every type",
     "text": "Phases, milestones, dashboards, tiles and curriculum modules are all rows here. The kind is enforced "
             "per type by a composite foreign key to deliverable_kinds, and the denormalized engagement_type_key "
             "is itself pinned to the engagement's type, so it can't drift.",
     "ref": "§2 row 3"},
    {"table": "deliverables", "title": "Two assignees, two organisations",
     "text": "internal_assignee_user_id is who does the work; client_owner_user_id is who signs it off on the client "
             "side. One column couldn't hold both, and the client owner is also what scopes an assigned-only "
             "portal user.",
     "ref": "§6 assignee_user_id split"},
    {"table": "deliverable_kinds", "title": "Structure is data, not templates",
     "text": "parent_kind makes the ≤2-level trees (dashboard → tile) enforceable; client_visible decides what the "
             "portal may ever show; bulk_child_counts lets a 99-tile dashboard be tracked mostly by counts.",
     "ref": "§6 PoC provisional answers · dashboards tracked by count"},
    {"table": "deliverable_blockers", "title": "Dependencies are edges; their state is derived",
     "text": "Only the edges are stored. blocked / maybe_unblocked / waiting / clear is computed at read time from "
             "the edges plus the manual flag, and a stale flag is surfaced for a human, never auto-cleared.",
     "ref": "§6 dep_state definitions confirmed"},
    {"table": "engagement_memberships", "title": "Visibility lives on the engagement",
     "text": "Seeing an engagement is a membership, never implied by the client. The owner is the single 'owner' "
             "membership (a partial unique index), not a column, so there's one source of truth. viewer_scope lets "
             "a client's project lead see everything while their QA sees only what they own.",
     "ref": "§2 row 6 · §6 owner_user_id dropped"},
    {"table": "access_roles", "title": "Module access: roles grant use/manage per module",
     "text": "Which areas someone can enter is a second, separate axis from record access. Roles bundle per-module "
             "grants, people hold any number, and the highest level wins, so every combination works and a new "
             "module is one row plus grants.",
     "ref": "§6 Module-based access and the CRM slice"},
    {"table": "users", "title": "No role enum",
     "text": "is_admin bypasses everything and is the only thing that edits access; is_contractor is an employment "
             "fact the client-invite rule reads. Everything else a person can do comes from their access roles.",
     "ref": "§6 Module-based access"},
    {"table": "deliverable_comments", "title": "Two channels, two tables",
     "text": "Internal notes (deliverable_activity) and the client thread (deliverable_comments) are separate tables, "
             "not one table with a visibility flag: a flag is one mis-set value away from leaking internal "
             "chatter to a client.",
     "ref": "§3 Client-facing portal"},
    {"table": "deliverable_client_reviews", "title": "Verdicts are history, and never move the pipeline",
     "text": "Each review is a new row (UAT is iterative); the latest wins. An 'accepted' never marks a deliverable "
             "done by itself: completion stays an explicit human decision.",
     "ref": "§6 Portal scope expanded"},
    {"table": "events", "title": "The event spine",
     "text": "Every write appends its event in the same transaction through one helper, guarded at runtime. A "
             "trigger makes the table append-only. The forecast's movement and future rules (stage age, alerts) "
             "read from here instead of adding timestamp columns.",
     "ref": "§2 row 5"},
    {"table": "opportunities", "title": "Deals on the same clients, bridged to delivery",
     "text": "Opportunities are separate from engagements; a prospect is just a client with no engagement yet. "
             "There is no amount column: it's always the sum of the line items. Probability and forecast "
             "category default from the stage and can be overridden while open.",
     "ref": "§3 Opportunities (CRM)"},
    {"table": "opportunity_line_items", "title": "A line item keeps its own price",
     "text": "Quantity × unit price is stored per line, so editing a product's default price never reprices a "
             "deal already on the books.",
     "ref": "§3 Opportunities (CRM)"},
    {"table": "engagements", "title": "Won → engagement is a bridge, not a merge",
     "text": "engagements.opportunity_id points at the won deal it delivers (one deal can have several). A won "
             "deal with nothing linked is flagged until someone sets delivery up.",
     "ref": "§6 Module-based access and the CRM slice"},
    {"table": "opportunity_stages", "title": "The pipeline is configuration",
     "text": "Stages, default probabilities and forecast categories are rows, a placeholder list that's easy to "
             "change before real data goes in.",
     "ref": "§6 Module-based access and the CRM slice"},
    # Designed, not built yet — shown when "Show planned" is on.
    {"table": "meetings", "title": "Meeting matching in two nullable stages",
     "text": "client_id and engagement_id are resolved separately and each may stay NULL: refuse to guess at every "
             "tier, and land the rest in an unassigned-meetings screen.",
     "ref": "§2 row 8"},
    {"table": "generated_content", "title": "LLM output is a cache, never generated on a GET",
     "text": "Most kinds are keyed to a meeting/engagement/client, so the second viewer reads the first viewer's "
             "row for free; input_hash skips regeneration when nothing changed.",
     "ref": "§3 Generated content · §6 mark_at_risk"},
    {"table": "llm_content_preferences", "title": "Opt-in per kind, engagement over user, default off",
     "text": "No row means disabled, which doubles as the spend-control lever. Opt-in gates generating, never "
             "reading.",
     "ref": "§2 row 15 · §6 Status ladder redesign"},
    {"table": "doc_chunks", "title": "Internal content can't leak into a link",
     "text": "url is only ever set for public 'web' rows, so nothing generated for a client can cite an internal "
             "playbook page: there's no URL to cite.",
     "ref": "§3 Knowledge base"},
    {"table": "time_entries", "title": "Blocked hours are computed, not dropped",
     "text": "Without a Harvest project the entry is still stored in full with a blocked_reason, and posted "
             "automatically once ops maps the project.",
     "ref": "§6 Harvest project resolution"},
]
