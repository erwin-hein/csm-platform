"""Admin-only schema explorer, a PoC demo aid (/admin/schema).

Joins two sources and shows where they disagree:
- the live database (SQLAlchemy reflection): the tables, columns, keys, constraints, triggers and
  row counts that actually exist, so the diagram can't drift from the migrations;
- CLAUDE.md §3 (app.schema_doc): each table's section, the SQL comments explaining it, and the
  tables and columns that are designed but not built yet.
Design rationale comes from app.schema_notes.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.access import require_admin
from app.db import get_db
from app.models import User
from app.schema_doc import load_spec_tables
from app.schema_notes import DECISIONS
from app.web.auth import current_user
from app.web.templating import render

router = APIRouter()

IGNORED_TABLES = {"alembic_version"}


def _type_name(col_type) -> str:
    if getattr(col_type, "enums", None):          # a Postgres enum reflects as a sized VARCHAR otherwise
        return f"{getattr(col_type, 'name', None) or 'enum'} ({', '.join(col_type.enums)})"
    try:
        return str(col_type).lower()
    except Exception:  # some reflected types can't compile without a dialect
        return col_type.__class__.__name__.lower()


def build_schema_map(db: Session) -> dict:
    conn = db.connection()
    insp = inspect(conn)
    spec = load_spec_tables()
    built = [t for t in insp.get_table_names() if t not in IGNORED_TABLES]
    triggers: dict[str, list[str]] = {}
    for table, name in conn.execute(text(
            "SELECT tgrelid::regclass::text, tgname FROM pg_trigger WHERE NOT tgisinternal")):
        triggers.setdefault(table, []).append(name)

    tables, edges = [], []
    order = {name: i for i, name in enumerate(spec)}  # the order CLAUDE.md tells them in
    for name in sorted(set(built) | set(spec), key=lambda n: (order.get(n, 999), n)):
        doc = spec.get(name)
        is_built = name in built
        entry = {"name": name, "section": doc.section if doc else "PoC additions", "built": is_built,
                 "notes": doc.notes if doc else [], "columns": [], "checks": [], "uniques": [], "indexes": [],
                 "triggers": triggers.get(name, []), "rows": None, "in_spec": doc is not None}
        if is_built:
            pk = set(insp.get_pk_constraint(name).get("constrained_columns") or [])
            fks = insp.get_foreign_keys(name)
            fk_by_col = {}
            for fk in fks:
                edges.append({"from": name, "cols": fk["constrained_columns"], "to": fk["referred_table"],
                              "to_cols": fk["referred_columns"], "planned": False})
                for c in fk["constrained_columns"]:
                    fk_by_col.setdefault(c, [])
                    if fk["referred_table"] not in fk_by_col[c]:
                        fk_by_col[c].append(fk["referred_table"])
            for c in insp.get_columns(name):
                dc = doc.columns.get(c["name"]) if doc else None
                entry["columns"].append({
                    "name": c["name"], "type": _type_name(c["type"]), "nullable": c["nullable"],
                    "pk": c["name"] in pk, "fk": (fk_by_col.get(c["name"]) or [None])[0],
                    "fks": fk_by_col.get(c["name"], []),
                    "default": str(c["default"]) if c.get("default") is not None else None,
                    "comment": dc.comment if dc else "", "status": "built" if dc or not doc else "poc_addition",
                })
            built_cols = {c["name"] for c in entry["columns"]}
            for dc in (doc.columns.values() if doc else []):
                if dc.name not in built_cols:
                    entry["columns"].append({"name": dc.name, "type": dc.type.lower(), "nullable": True, "pk": False,
                                             "fk": dc.references, "fks": [dc.references] if dc.references else [],
                                             "default": None, "comment": dc.comment,
                                             "status": "superseded" if dc.deprecated else "spec_only"})
            entry["checks"] = [c.get("sqltext") for c in insp.get_check_constraints(name)]
            entry["uniques"] = [u["column_names"] for u in insp.get_unique_constraints(name)]
            entry["indexes"] = [{"name": i["name"], "columns": i["column_names"], "unique": i["unique"],
                                 "where": (i.get("dialect_options") or {}).get("postgresql_where")}
                                for i in insp.get_indexes(name)]
            entry["rows"] = conn.execute(text(f'SELECT count(*) FROM "{name}"')).scalar()
        else:
            for dc in doc.columns.values():
                entry["columns"].append({"name": dc.name, "type": dc.type.lower(), "nullable": True,
                                         "pk": "primary key" in dc.type.lower(), "fk": dc.references,
                                         "fks": sorted({t for c, t in doc.fks if c == dc.name}),
                                         "default": None, "comment": dc.comment,
                                         "status": "superseded" if dc.deprecated else "planned"})
            seen = set()
            for col, target in doc.fks:
                if (col, target) not in seen:
                    seen.add((col, target))
                    edges.append({"from": name, "cols": [col], "to": target, "to_cols": [], "planned": True})
        tables.append(entry)

    known = {t["name"] for t in tables}
    sections: list[str] = []
    for t in tables:
        if t["section"] not in sections:
            sections.append(t["section"])
    return {"tables": tables, "edges": [e for e in edges if e["to"] in known], "sections": sections,
            "decisions": [d for d in DECISIONS if d["table"] in known]}


@router.get("/admin/schema")
def schema_explorer(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_admin(user)
    data = build_schema_map(db)
    return render(request, "admin/schema.html", nav="schema", schema=data,
                  built_count=sum(t["built"] for t in data["tables"]),
                  planned_count=sum(not t["built"] for t in data["tables"]))
