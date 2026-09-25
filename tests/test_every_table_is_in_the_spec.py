"""Doc-drift guard: CLAUDE.md is the source of truth, so every table the migrations create must be
described in §3, and every design note on the schema explorer must point at a table that exists
(built or designed). Built columns missing from the spec show up on the explorer as PoC additions."""

from app.schema_doc import load_spec_tables
from app.schema_notes import DECISIONS
from app.web.schema import build_schema_map


def test_every_built_table_is_described_in_claude_md(db):
    m = build_schema_map(db)
    undocumented = [t["name"] for t in m["tables"] if t["built"] and not t["in_spec"]]
    assert undocumented == [], f"Describe these in CLAUDE.md §3: {undocumented}"


def test_design_notes_point_at_real_tables(db):
    names = {t["name"] for t in build_schema_map(db)["tables"]}
    assert {d["table"] for d in DECISIONS} <= names


def test_spec_parser_reads_sections_comments_and_keys():
    spec = load_spec_tables()
    d = spec["deliverables"]
    assert d.section == "Deliverables"
    assert "client_owner_user_id" in d.columns and "sign-off" in d.columns["client_owner_user_id"].comment
    assert ("kind", "deliverable_kinds") in d.fks                         # composite FK
    assert spec["engagement_memberships"].columns["viewer_scope"].type.startswith("TEXT")  # from ALTER TABLE
    assert spec["users"].columns["llm_content_default"].deprecated
    assert any("owner" in n for n in spec["engagement_memberships"].notes)
    assert not spec["meetings"].notes or "Fathom" not in spec["meetings"].notes[0]   # belongs to meeting_action_items
    assert set(spec["access_roles"].columns) == {"id", "name", "description", "is_default", "created_at"}
