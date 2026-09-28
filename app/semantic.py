"""The compiled Malloy semantic model, as the app sees it (CLAUDE.md §3 Semantic layer).

analytics/compile.mjs compiles analytics/models/csm.malloy against Postgres and writes
analytics/build/csm.model.json: each source's fields and docs, and each named view with its Malloy
text and compiled SQL. The app never needs Node: it reads that file, shows the model on an admin
page, and can run a view's precompiled SQL.

Running is deliberately narrow: only SQL that came out of the compiled model (never user input),
only statements that read, inside a savepoint that is always rolled back, with a timeout and a row cap.
"""

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent / "analytics"
MODEL_FILE = ROOT / "models" / "csm.malloy"
BUILD_FILE = ROOT / "build" / "csm.model.json"
ROW_LIMIT = 200
TIMEOUT_MS = 5000


class SemanticModelMissing(Exception):
    pass


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_model() -> dict:
    if not BUILD_FILE.exists():
        raise SemanticModelMissing(f"{BUILD_FILE.relative_to(ROOT.parent)} is missing: run `npm run compile` in analytics/")
    return _load(str(BUILD_FILE), BUILD_FILE.stat().st_mtime)


def is_stale(model: dict) -> bool:
    """The .malloy file was edited after the last compile."""
    if not MODEL_FILE.exists():
        return False
    return hashlib.sha256(MODEL_FILE.read_bytes()).hexdigest() != model["model_sha256"]


def find_view(model: dict, source: str, name: str) -> dict | None:
    return next((v for v in model["views"] if v["source"] == source and v["name"] == name), None)


@dataclass
class ViewResult:
    columns: list[str]
    rows: list[list]
    truncated: bool


def run_view(db: Session, view: dict) -> ViewResult:
    sql = view["sql"].strip().rstrip(";")
    if not sql.lstrip("(").upper().startswith(("SELECT", "WITH")):
        raise ValueError("Only read queries from the compiled model can run here")
    savepoint = db.begin_nested()
    try:
        db.execute(text(f"SET LOCAL statement_timeout = {TIMEOUT_MS}"))
        # Run as compiled (wrapping it in a LIMIT subquery wouldn't keep its ORDER BY) and read
        # one row past the cap to know whether it was truncated.
        result = db.execute(text(sql))
        keys, raw = list(result.keys()), result.fetchmany(ROW_LIMIT + 1)
    finally:
        savepoint.rollback()
    # Malloy's Postgres SQL returns each row as one JSON object in a column named "row".
    if keys == ["row"] and all(isinstance(r[0], dict) for r in raw):
        columns = list(raw[0][0].keys()) if raw else []
        rows = [[r[0].get(c) for c in columns] for r in raw]
    else:
        columns, rows = keys, [list(r) for r in raw]
    return ViewResult(columns, rows[:ROW_LIMIT], len(rows) > ROW_LIMIT)
