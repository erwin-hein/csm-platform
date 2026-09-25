# PoC demo: recording plan

A skeleton to ad-lib from, not a script to read. Each act has **who** you're signed in as, **where** to click, and
**say**: the one or two decisions worth narrating there. Everything named below exists in the seed data.

**Running time:** about 25 minutes. A 10-minute cut is at the end.

## The shape: follow the work, not the menu

Don't tour the app as the admin. Morgan sees everything, which hides the thing most worth showing: that each person
sees exactly their slice. Instead, follow one piece of work through its life (a deal is sold, handed off,
delivered, reviewed by the client) and switch to whoever does that step. The access model then shows itself at every
switch, and you don't need a separate "limitations" segment. Admin appears only at the edges: the schema map at the
start, the Access and Event log screens near the end, and the schema deep dive to close.

| # | Act | Signed in as | ~min |
|---|---|---|---|
| 0 | Why this exists | none (slide or voice-over) | 1 |
| 1 | The map | Morgan (admin): Schema | 3 |
| 2 | Selling | Nadia (Sales lead) | 4 |
| 3 | Handoff | Elena (Operations) | 3 |
| 4 | Delivering | Tomás (Delivery + Resourcing) | 5 |
| 5 | The client | Marcus, then Dr. Hannah Cho (client users) | 3 |
| 6 | Who sees what | Rafael, Aisha, then Morgan: Access | 2 |
| 7 | The spine | Morgan: Event log | 1 |
| 8 | Schema deep dive, and what's next | Morgan: Schema | 5 |

## Before you record

- **Reset the data** right before recording: `docker compose down -v && docker compose up --build`. Acts 3 and 5
  change data. Rehearse once, then reset again.
- **Switching people fast.** Sign in at `/demo-login` (passcode `demo`). To avoid logging in and out on camera, keep
  one browser profile per person, or use both `http://localhost:8000` and `http://127.0.0.1:8000`: they keep
  separate sessions, so two people can be open side by side.
- Browser zoom around 90%, window at least 1400px wide. On the Schema page, click **Reset layout** and then
  **Fit** so it starts clean.
- Seed dates are relative to the day you seed, so "overdue", "won 3 days ago" and the forecast quarter all line
  up on recording day.

---

## 0 · Why this exists (1 min, no screen needed)

**Say**
- An internal platform to track client engagements. It rebuilds Hao's single-user tool as a multi-user one.
- Two structural fixes drive the whole design:
  1. The old tool treated "client" and "the work we're doing for them" as one record. Here they're separate.
  2. The old tool was shaped around one person's habits. This one has roles, teams, contractors and client viewers
     from day one.
- It's a PoC, and most of the effort went into the schema, which is the part that carries forward. So I'll start
  and end there.

## 1 · The map (3 min): Morgan, **Schema**

**Show**
- The whole diagram. The coloured groups are the sections of the spec. 22 tables are built; the spec has 18 more.
- Click decision **1, "Client ≠ Engagement"**. The diagram zooms to `clients` and everything that points at it.
- Click **"An engagement type is a contract"**.

**Say**
- This page is built from the live database and the spec document, so it can't go stale. If someone adds a table
  without documenting it, a test fails.
- A client is just identity. Everything time-bound hangs off an engagement.
- Engagement types (QuickStart, Migration) are rows, not code. A new type means registering data, not forking the
  app.

Don't go deep yet. Tell them you'll come back to it at the end.

## 2 · Selling (4 min): **Nadia Osei** (Sales lead)

**Show**
1. Point at the nav first: only Pipeline, Forecast, Clients and Event log. No engagements at all, because Nadia is
   sales only.
2. **Pipeline**: one column per stage with $ totals and weighted totals. Move *Atlas Freight QuickStart* from
   Discovery to Proposal with the stage picker on its card.
3. Open *Juniper Tableau → Omni Migration*. It has two products, and the amount is their sum. Its stage (Proposal)
   would put it in Best case, but the rep overrode the forecast category to Commit.
4. **Forecast**:
   - Commit forecast vs best-case forecast, by owner.
   - The Outlook table.
   - **Recent movement**: *Northwind Creator Training*'s close date slipped.
5. Point at the banner: *Cobalt Store Ops Dashboards — Phase 2* is won, but no engagement delivers it yet.

**Say**
- Opportunities sit on the same clients as engagements. A prospect is just a client with no engagement yet.
- There's no amount column: it's always the sum of the products, so it can't drift.
- Stage columns here, but not on the engagement boards (you'll see why in act 4). A deal is only ever in one stage.
- The movement feed isn't extra tables. It's read from the event log that every write already goes through.
- The flag is the handoff that hasn't happened. That's the next act.

## 3 · Handoff (3 min): **Elena Park** (Operations)

**Show**
1. Portfolio: the same won-but-undelivered flag appears here too.
2. Open the flagged opportunity, then **+ Create engagement from this**. Client and name come prefilled from the
   deal. Create it, and the flag is gone.
3. **Team → By engagement**: *Harbor & Pine Power BI → Omni Migration* is flagged "Needs assignment". Assign an
   owner.

**Say**
- Elena is Operations, not admin. Operations is just a role that grants *manage* on every module. Only admin
  can change who has which role.
- The won deal and the engagement are linked, not merged: one deal can turn into several engagements.
- Being on an engagement's team is what makes it visible to someone. Nothing is implied by the client.

## 4 · Delivering (5 min): **Tomás Alvarez** (Delivery + Resourcing)

**Show**
1. The nav now has Portfolio, the boards and Team, but no Pipeline. Tomás holds two roles; that's how combinations
   work.
2. **Migration board**: one card per engagement. Try **Group by** and **Sort by**. Bluefin shows *Semantic-layer
   Parity* and *Dashboard Build* active at the same time.
3. Open *Bluefin Tableau → Omni Migration*:
   - The stage bar follows the phases below. Nobody moves it by hand.
   - *Clinical Operations Workbook*: 99 tiles, 2 listed individually and 97 tracked by count (**Edit counts**).
   - Dependency signals: *Denial rate by payer* **Blocked** (red), *Bed occupancy trend* **Maybe unblocked**
     (yellow), and the quiet grey "after X" for ordinary sequencing.
   - The 💬 on *Daily census KPI*: *Internal notes* and *Client thread* are separate tabs, in different colours.
   - The **Client view** tab shows exactly what the client lead sees. The **Client access** panel shows who has
     access, at which scope.
4. Quickly open the **QuickStart board**: it's grouped by stage by default, because QuickStart stages are set by
   hand and run one at a time.

**Say**
- There's one deliverable engine for every type. Phases, milestones, dashboards, tiles and curriculum modules are all
  the same table, with the allowed kinds enforced per type in the database.
- Stages can run concurrently because they're derived from the work. Modeling and dashboarding happen in tandem,
  and there are no gate stages.
- Tracking tiles by count exists so the system actually gets used on a 99-tile dashboard.
- Waiting on something that hasn't started is just planned order, so it stays quiet. Only real blocks are loud.
- Internal notes and client comments live in separate tables, not one table with a flag. A mis-set flag would leak
  internal chatter to the client.

## 5 · The client (3 min): **Marcus Reid**, then **Dr. Hannah Cho**

**Show**
1. Marcus (the client's QA, *assigned only*):
   - He lands on the portal and sees only the tiles he owns, with their dashboards shown for context.
   - *Daily census KPI* is in UAT: it shows his earlier rejection and Tomás's reply. Accept it.
   - *Bed occupancy trend* isn't in UAT: he can comment, but there are no verdict buttons.
2. Hannah (the client's project lead, *full* scope): she sees every dashboard and tile.
3. Optional: back in Tomás's window, the verdict shows up, but the tile is **not** marked done.

**Say**
- Client access is invite-only and per engagement. The client decides who's lead and who's QA, not us.
- Verdicts can only be given in UAT, and they're a history: reject, fix, re-review.
- An "accepted" never completes the work on its own. Completion stays a human decision on our side.
- Phases and milestones never appear in the portal. Which kinds of deliverable a client can see is declared per
  type.

## 6 · Who sees what (2 min): Rafael, Aisha, then Morgan

**Show**
1. **Rafael Costa** (Sales only): lands straight on the pipeline and has no engagements anywhere. Rafael can open
   any deal, but can only edit his own.
2. **Aisha Bello** (Delivery only): opening `/pipeline` gives a 404. Her portfolio shows only the engagements
   she's on.
3. **Morgan → Access**:
   - The roles × modules matrix, each cell *use* or *manage*.
   - People can hold several roles, and "Effective access" shows the result.
   - Give **Delivery** `use` on Opportunities, refresh Aisha's window: she has a Pipeline now. Undo it.

**Say**
- Two separate questions: *which areas can you enter* (module roles) and *which records inside them* (team
  membership, deal ownership).
- The answer to the "new membership type for sales?" question was no: that would mix the two.
- A new module later is one row plus grants. Nothing about existing users needs touching.
- Anything you can't access returns 404, not "forbidden", so its existence doesn't leak either.

## 7 · The spine (1 min): Morgan, **Event log**

**Show** Everything you just did, newest first: the stage move, the handoff, the verdict, the grant.

**Say** Every write lands its event in the same transaction, through one helper. A write that forgets to raises
an error. The table is append-only, enforced by the database. Future features like rules, digests and alerts will
subscribe to this rather than reaching into each other's tables.

## 8 · Schema deep dive, and what's next (5 min): Morgan, **Schema**

**Show**
1. Walk a handful of decisions from the list (click each; the right panel carries the "why"):
   - **One tracking engine**. Point at the composite foreign keys under *Relationships*.
   - **Visibility lives on the engagement**. Under *Enforced in the database*, point at the partial unique index
     that means one owner per engagement.
   - **Two channels, two tables**.
   - **The event spine**. Its two triggers make it append-only.
   - **Deals on the same clients**. No amount column.
2. Search `client_owner`: the column lights up in `deliverables`. It's what scopes a client QA user.
3. Hover a foreign-key row to show the table it points at, and drag a table around. Tell them this page is for them
   to poke at after the call.
4. Turn on **Show planned**: 18 more tables fade in. Click **Meeting matching in two nullable stages** and **LLM
   output is a cache**.
5. The *Spec vs. database* list in the side panel (**← All decisions**): e.g. `users.onboarded_at` is designed but
   not built yet.

**Say**
- The dashed tables are the next iterations: meetings sync, LLM drafting, Harvest time, the rules engine, the
  assistant. They already hang off the same core, so the foundation doesn't change when they arrive.
- The spec document is the source of truth. Every open question gets settled there, with the reasoning logged.
- Close with what's deliberately deferred (quotas, multi-currency, auto-discovery, managed auth) and what you want
  from the audience.

---

## 10-minute cut

1. **Why** (30s) and **the map** (1 min).
2. **Nadia**: pipeline, the Juniper deal, the won-but-undelivered banner (2 min).
3. **Elena**: create the engagement from the deal (1 min).
4. **Tomás**: Bluefin's concurrent stages, the 99-tile count, the two comment channels (2.5 min).
5. **Marcus**: sees only his tiles, verdict only in UAT (1.5 min).
6. **Morgan**: the Access matrix, then Schema with *Show planned* (2 min).

## If something goes sideways

- **A page shows old data:** you're probably in another person's session. Check the name top right.
- **Recording on a quarter's last days:** the forecast's current quarter is thin. Use **Next ›**, or switch the
  period to Month.
- **You changed data during rehearsal:** `docker compose down -v && docker compose up` restores the exact seed.
