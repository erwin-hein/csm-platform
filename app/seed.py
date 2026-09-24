"""Demo seed data. Goes through the real service functions (not raw inserts), so
every seeded row also lands in `events` exactly as a user action would.

    uv run python -m app.seed           # seeds only if the database has no clients yet
    uv run python -m app.seed --reset   # wipes PoC tables first (events included) — local/dev only

All names and companies are fictional.
"""

import sys
from datetime import date, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Client, Deliverable, User
from app.services import clients as C
from app.services import deliverables as D
from app.services import engagements as E
from app.services import memberships as M
from app.services import users as U

TODAY = date.today()


def _d(days: int) -> date:
    return TODAY + timedelta(days=days)


USERS = [
    # email, display name, role
    ("morgan.ellis@shearwaterdata.com", "Morgan Ellis", "admin"),
    ("priya.raman@shearwaterdata.com", "Priya Raman", "analyst"),
    ("tomas.alvarez@shearwaterdata.com", "Tomás Alvarez", "analyst"),
    ("aisha.bello@shearwaterdata.com", "Aisha Bello", "analyst"),
    ("jordan.kim@shearwaterdata.com", "Jordan Kim", "contractor"),
]

QS_MODULES = [
    "Omni fundamentals & navigation",
    "Modeling layer: topics & views",
    "Workbook building",
    "Dashboards & filters",
    "Permissions & content sharing",
    "Scheduling & alerts",
    "Embedding basics",
    "Creator self-serve habits",
]


def _set(db, admin, d: Deliverable, **fields) -> None:
    D.update_deliverable(db, admin, d.id, **fields)


def seed_quickstart(db: Session, admin: User, eng_id, progress: dict[int, str], *, assignee=None,
                    blocked: dict[int, str] | None = None, na: set[int] = frozenset(),
                    blocker_edges: list[tuple[int, int]] = ()) -> list[Deliverable]:
    mods = [
        D.create_deliverable(db, admin, eng_id, kind="module", name=name, internal_assignee_user_id=assignee,
                             target_date=_d(7 * (i - 2)), hours_estimated="2")
        for i, name in enumerate(QS_MODULES)
    ]
    for i, status in progress.items():
        _set(db, admin, mods[i], pipeline_status=status)
    for i in na:
        _set(db, admin, mods[i], not_applicable=True)
    for blocked_i, blocker_i in blocker_edges:
        D.add_blocker(db, admin, mods[blocked_i].id, blocker_id=mods[blocker_i].id)
    for i, reason in (blocked or {}).items():
        _set(db, admin, mods[i], blocked=True, blocked_reason=reason)
    return mods


def seed_migration(db: Session, admin: User, eng_id, spec: dict, *, assignee=None) -> dict[str, Deliverable]:
    """spec = {"phases": [(name, status, [(milestone, status), ...])], "dashboards": [(name, status, [(tile, status)])]}"""
    by_name: dict[str, Deliverable] = {}
    for parent_kind, child_kind, key in (("phase", "milestone", "phases"), ("dashboard", "tile", "dashboards")):
        for i, (name, status, children) in enumerate(spec.get(key, [])):
            parent = D.create_deliverable(db, admin, eng_id, kind=parent_kind, name=name,
                                          internal_assignee_user_id=assignee, target_date=_d(14 * i - 14),
                                          priority="high" if i == 0 else "medium")
            if status != "not_started":
                _set(db, admin, parent, pipeline_status=status)
            by_name[name] = parent
            for cname, cstatus in children:
                child = D.create_deliverable(db, admin, eng_id, kind=child_kind, name=cname, parent_id=parent.id,
                                             internal_assignee_user_id=assignee, hours_estimated="4")
                if cstatus != "not_started":
                    _set(db, admin, child, pipeline_status=cstatus)
                by_name[cname] = child
    return by_name


def seed(db: Session) -> None:
    users = {}
    for email, name, role in USERS:
        users[name.split()[0].lower()] = U.get_user_by_email(db, email) or U.create_internal_user(
            db, None, email=email, display_name=name, role=role)
    admin, priya, tomas, aisha, jordan = (users[k] for k in ("morgan", "priya", "tomás", "aisha", "jordan"))

    # ---------------- clients
    northwind = C.create_client(db, admin, name="Northwind Logistics", domains="northwindlogistics.com")
    bluefin = C.create_client(db, admin, name="Bluefin Health", domains="bluefinhealth.org, bluefin.health")
    cobalt = C.create_client(db, admin, name="Cobalt Retail Group", domains="cobaltretail.com")
    harbor = C.create_client(db, admin, name="Harbor & Pine Insurance", domains="harborpine.com")
    meridian = C.create_client(db, admin, name="Meridian Energy", domains="meridianenergy.co")

    C.add_alias(db, admin, northwind.id, alias="NWL", alias_type="acronym")
    C.add_alias(db, admin, bluefin.id, alias="bluefin-health", alias_type="slack_slug")
    C.add_alias(db, admin, harbor.id, alias="H&P", alias_type="acronym")
    for client, name, email, title in [
        (northwind, "Dana Whitaker", "dana.whitaker@northwindlogistics.com", "Head of BI"),
        (northwind, "Leo Marsh", "leo.marsh@northwindlogistics.com", "Analytics Engineer"),
        (bluefin, "Dr. Hannah Cho", "hcho@bluefinhealth.org", "VP Data"),
        (bluefin, "Marcus Reid", "mreid@bluefinhealth.org", "BI Lead"),
        (cobalt, "Sofia Brandt", "sofia.brandt@cobaltretail.com", "Director of Analytics"),
        (harbor, "Ian Gallagher", "igallagher@harborpine.com", "Data Platform Manager"),
        (meridian, "Ruth Adeyemi", "ruth.adeyemi@meridianenergy.co", "Analytics Manager"),
    ]:
        C.add_contact(db, admin, client.id, name=name, email=email, title=title)

    # ---------------- QuickStart engagements
    nw_qs = E.create_engagement(db, admin, client_id=northwind.id, type_key="quickstart",
                                name="Northwind QuickStart — 2026")
    E.change_stage(db, admin, nw_qs.id, stage="dev_training")
    E.change_stage(db, admin, nw_qs.id, stage="codev")
    M.assign_member(db, admin, nw_qs.id, user_id=priya.id, role="owner")
    M.assign_member(db, admin, nw_qs.id, user_id=jordan.id, role="collaborator")
    nw_mods = seed_quickstart(db, admin, nw_qs.id, {0: "done", 1: "done", 2: "external_validation",
                                                     3: "in_progress", 4: "internal_validation"},
                              assignee=priya.id, na={6}, blocker_edges=[(5, 4)])
    D.add_note(db, priya, nw_mods[2].id, body="Leo built two workbooks on his own in co-dev — sent for Dana's review.")

    cobalt_qs = E.create_engagement(db, admin, client_id=cobalt.id, type_key="quickstart",
                                    name="Cobalt QuickStart — Store Ops")
    E.change_stage(db, admin, cobalt_qs.id, stage="dev_training")
    M.assign_member(db, admin, cobalt_qs.id, user_id=tomas.id, role="owner")
    seed_quickstart(db, admin, cobalt_qs.id, {0: "done", 1: "in_progress"}, assignee=tomas.id,
                    blocked={1: "Client's dbt project not yet connected — waiting on their IT to grant warehouse access"})

    meridian_qs = E.create_engagement(db, admin, client_id=meridian.id, type_key="quickstart",
                                      name="Meridian QuickStart")
    M.assign_member(db, admin, meridian_qs.id, user_id=aisha.id, role="owner")
    M.assign_member(db, admin, meridian_qs.id, user_id=priya.id, role="viewer")
    seed_quickstart(db, admin, meridian_qs.id, {0: "in_progress"}, assignee=aisha.id)

    # a completed one, for history (hidden from boards by default)
    nw_old = E.create_engagement(db, admin, client_id=northwind.id, type_key="quickstart",
                                 name="Northwind QuickStart pilot — 2025")
    for stage in ("dev_training", "codev", "creator_training", "wrapup", "handed_off"):
        E.change_stage(db, admin, nw_old.id, stage=stage)
    E.change_status(db, admin, nw_old.id, status="complete")
    M.assign_member(db, admin, nw_old.id, user_id=priya.id, role="owner")
    seed_quickstart(db, admin, nw_old.id, {i: "done" for i in range(8)}, assignee=priya.id)

    # ---------------- Migration engagements
    bf = E.create_engagement(db, admin, client_id=bluefin.id, type_key="migration",
                             name="Bluefin Tableau → Omni Migration")
    for stage in ("access_setup", "semantic_parity"):
        E.change_stage(db, admin, bf.id, stage=stage)
    M.assign_member(db, admin, bf.id, user_id=tomas.id, role="owner")
    M.assign_member(db, admin, bf.id, user_id=priya.id, role="collaborator")
    bfx = seed_migration(db, admin, bf.id, {
        "phases": [
            ("Scoping", "done", [("Inventory of 42 Tableau workbooks", "done"), ("Signed-off migration scope", "done")]),
            ("Access & Setup", "done", [("Snowflake service account", "done"), ("Omni SSO configured", "done")]),
            ("Semantic-layer Parity", "in_progress", [
                ("Core patient-encounter model", "internal_validation"),
                ("Revenue-cycle measures", "in_progress"),
                ("Parity sign-off vs. Tableau extracts", "not_started"),
            ]),
            ("Dashboard Build", "not_started", [("Rebuild priority dashboards", "not_started")]),
        ],
        "dashboards": [
            ("Executive Census", "in_progress", [
                ("Daily census KPI", "external_validation"), ("Bed occupancy trend", "internal_validation"),
                ("Admissions by service line", "in_progress"), ("Discharge forecast", "not_started"),
            ]),
            ("Revenue Cycle", "not_started", [
                ("Days in A/R", "not_started"), ("Denial rate by payer", "not_started"),
                ("Clean-claim rate", "not_started"),
            ]),
        ],
    }, assignee=tomas.id)
    # Real blocker edges: dashboard work waits on semantic-layer parity.
    D.add_blocker(db, admin, bfx["Parity sign-off vs. Tableau extracts"].id, blocker_id=bfx["Revenue-cycle measures"].id)
    D.add_blocker(db, admin, bfx["Revenue Cycle"].id, blocker_id=bfx["Revenue-cycle measures"].id)
    D.add_blocker(db, admin, bfx["Rebuild priority dashboards"].id,
                  blocker_id=bfx["Parity sign-off vs. Tableau extracts"].id)
    _set(db, admin, bfx["Denial rate by payer"], blocked=True,
         blocked_reason="Payer-mapping table owned by client's RCM team; no ETA")
    D.add_blocker(db, admin, bfx["Denial rate by payer"].id, blocker_id=bfx["Revenue-cycle measures"].id)
    # A flagged block whose only blocker is already done -> derived 'maybe_unblocked'.
    D.add_blocker(db, admin, bfx["Bed occupancy trend"].id, blocker_id=bfx["Core patient-encounter model"].id)
    _set(db, admin, bfx["Core patient-encounter model"], pipeline_status="done")
    _set(db, admin, bfx["Bed occupancy trend"], blocked=True,
         blocked_reason="Waiting for encounter model to pass internal validation")
    D.add_note(db, tomas, bfx["Revenue-cycle measures"].id,
               body="Net-collection-rate differs from Tableau by 0.4% — tracing to a date-grain mismatch.")

    cb_mig = E.create_engagement(db, admin, client_id=cobalt.id, type_key="migration",
                                 name="Cobalt Looker → Omni Migration")
    for stage in ("access_setup", "semantic_parity", "dashboard_build"):
        E.change_stage(db, admin, cb_mig.id, stage=stage)
    M.assign_member(db, admin, cb_mig.id, user_id=priya.id, role="owner")
    M.assign_member(db, admin, cb_mig.id, user_id=aisha.id, role="collaborator")
    cbx = seed_migration(db, admin, cb_mig.id, {
        "phases": [
            ("Scoping", "done", [("LookML inventory", "done")]),
            ("Access & Setup", "done", [("BigQuery connection", "done")]),
            ("Semantic-layer Parity", "done", [("Port 18 LookML views", "done"), ("Parity sign-off", "done")]),
            ("Dashboard Build", "in_progress", [("Store performance suite", "in_progress"),
                                                ("Merchandising suite", "not_started")]),
            ("Client Validation", "not_started", [("UAT with store ops", "not_started")]),
        ],
        "dashboards": [
            ("Store Performance", "internal_validation", [
                ("Sales vs. plan", "done"), ("Comp-store growth", "done"), ("Basket size", "internal_validation"),
            ]),
            ("Merchandising", "in_progress", [("Sell-through by category", "in_progress"), ("Markdown impact", "not_started")]),
            ("Inventory Health", "not_started", [("Weeks of supply", "not_started"), ("Stock-outs", "not_started")]),
        ],
    }, assignee=priya.id)
    D.add_blocker(db, admin, cbx["UAT with store ops"].id, blocker_id=cbx["Store performance suite"].id)
    D.add_blocker(db, admin, cbx["Markdown impact"].id, blocker_id=cbx["Sell-through by category"].id)

    # Deliberately left with NO memberships — assign someone to it live in the demo.
    hp = E.create_engagement(db, admin, client_id=harbor.id, type_key="migration",
                             name="Harbor & Pine Power BI → Omni Migration")
    seed_migration(db, admin, hp.id, {
        "phases": [("Scoping", "in_progress", [("Report inventory", "in_progress"), ("Scope sign-off", "not_started")]),
                   ("Access & Setup", "not_started", [])],
        "dashboards": [("Claims Overview", "not_started", [("Open claims", "not_started"), ("Loss ratio", "not_started")])],
    })


def reset(db: Session) -> None:
    # events is append-only by trigger; the reset is a dev-only escape hatch that
    # disables it for this transaction.
    db.execute(text("ALTER TABLE events DISABLE TRIGGER USER"))
    db.execute(text(
        "TRUNCATE events, deliverable_activity, deliverable_blockers, deliverables, engagement_memberships, "
        "engagements, client_contacts, client_aliases, clients, users"))
    db.execute(text("ALTER TABLE events ENABLE TRIGGER USER"))


def main() -> None:
    with SessionLocal() as db:
        if "--reset" in sys.argv:
            reset(db)
        elif db.scalar(select(Client.id).limit(1)):
            print("Database already has clients; skipping seed (use --reset to wipe and reseed).")
            return
        seed(db)
        db.commit()
        print("Seeded demo data.")


if __name__ == "__main__":
    main()
