"""Reads the schema as CLAUDE.md §3 describes it, for the admin schema explorer.

CLAUDE.md is the design source of truth, so the explorer takes from it what the database
can't tell us: which §3 section a table belongs to, the SQL comments explaining its columns
and design choices, and the tables that are designed but not built yet. The live database
(reflected in app/web/schema.py) stays authoritative for what actually exists.

The parser only understands the SQL style CLAUDE.md uses (CREATE TABLE blocks with one
column per line or a few comma-separated ones, ALTER TABLE ... ADD COLUMN, trailing --
comments). Anything it can't read is skipped, never guessed.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

SPEC_PATH = Path(__file__).resolve().parent.parent / "CLAUDE.md"

_CREATE = re.compile(r"^CREATE TABLE (\w+)\s*\(")
_ALTER = re.compile(r"^ALTER TABLE (\w+) ADD COLUMN (\w+) (.+?);\s*(?:--\s*(.*))?$")
_REF = re.compile(r"REFERENCES (\w+)\s*\((\w+)\)")
_TABLE_FK = re.compile(r"FOREIGN KEY \(([^)]*)\) REFERENCES (\w+)\s*\(([^)]*)\)")
_SKIP_WORDS = ("PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK", "CONSTRAINT")


@dataclass
class DocColumn:
    name: str
    type: str
    comment: str = ""
    references: str | None = None      # target table
    deprecated: bool = False


@dataclass
class DocTable:
    name: str
    section: str
    columns: dict[str, DocColumn] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    fks: list[tuple[str, str]] = field(default_factory=list)   # (column, target table)


def _split_code(line: str) -> tuple[str, str]:
    code, _, comment = line.partition("--")
    return code.strip(), comment.strip()


def _section_label(heading: str) -> str:
    # "Deliverables — the generalized tracking engine" → "Deliverables"
    return re.split(r" — |:", heading)[0].strip()


def _column_defs(code: str) -> list[str]:
    """Split a line's code into column definitions on top-level commas."""
    parts, depth, cur = [], 0, ""
    for ch in code:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def parse_spec(text: str) -> dict[str, DocTable]:
    tables: dict[str, DocTable] = {}
    section = ""
    in_schema = in_sql = False
    current: DocTable | None = None
    last_table: DocTable | None = None
    # Comment lines between statements: they introduce the next CREATE TABLE if one follows
    # straight away, otherwise they annotate the table above (e.g. a note on its index).
    pending: list[str] = []
    joined = False   # the previous line was a comment line of the same note

    def flush_to(table: DocTable | None) -> None:
        if table is not None:
            for note in pending:
                table.notes.append(note)
        pending.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if raw.startswith("## "):
            in_schema = raw.startswith("## 3.")
            continue
        if not in_schema:
            continue
        if raw.startswith("### "):
            section = _section_label(raw[4:])
            continue
        if line.startswith("```"):
            if in_sql:
                flush_to(last_table)
            in_sql = line == "```sql"
            current = last_table = None
            continue
        if not in_sql:
            continue

        if current is None:
            if line.startswith("--"):
                text_ = line.lstrip("- ").strip()
                if pending and joined and not text_.startswith("NOTE"):
                    pending[-1] += " " + text_
                else:
                    pending.append(text_)
                joined = True
                continue
            joined = False
            m = _CREATE.match(line)
            if m:
                name = m.group(1)
                if name in tables:          # a table repeated in a later section keeps its first home
                    current = DocTable(name, section)
                else:
                    current = tables[name] = DocTable(name, section)
                flush_to(current)
                rest = line[m.end():]
                code, comment = _split_code(rest)
                if code.endswith(");"):          # a one-line CREATE TABLE
                    _read_columns(current, code[:-2] + (f" -- {comment}" if comment else ""))
                    last_table, current = current, None
                elif code:
                    _read_columns(current, rest)
                elif comment:
                    current.notes.append(comment)
                continue
            if not line:
                if last_table is not None:      # a comment block followed by a blank line trails the table above
                    flush_to(last_table)
                continue
            flush_to(last_table)
            m = _ALTER.match(line)
            if m and m.group(1) in tables:
                t = tables[m.group(1)]
                col = DocColumn(m.group(2), m.group(3).split(" DEFAULT")[0].strip(), m.group(4) or "")
                t.columns.setdefault(col.name, col)
                continue
            if not line.startswith("CREATE UNIQUE INDEX") and not line.startswith("CREATE INDEX"):
                last_table = None            # an INSERT or another statement: stop attaching notes
            continue

        if line.startswith(")"):
            last_table, current = current, None
            joined = False
            continue
        if line.startswith("--"):
            _note(current, line.lstrip("- ").strip(), joined and not line.lstrip("- ").startswith("NOTE"))
            joined = True
            continue
        joined = False
        _read_columns(current, line)
    flush_to(last_table)
    return tables


def _note(table: DocTable, text: str, continues: bool) -> None:
    """Consecutive comment lines are one note."""
    if continues and table.notes:
        table.notes[-1] += " " + text
    else:
        table.notes.append(text)


def _read_columns(table: DocTable, line: str) -> None:
    code, comment = _split_code(line)
    if not code:
        if comment:
            table.notes.append(comment)
        return
    defs = _column_defs(code)
    for i, d in enumerate(defs):
        m = _TABLE_FK.search(d)
        if m:
            for col in (c.strip() for c in m.group(1).split(",")):
                table.fks.append((col, m.group(2)))
            continue
        if d.upper().startswith(_SKIP_WORDS):
            continue
        name, _, rest = d.partition(" ")
        if not re.fullmatch(r"\w+", name):
            continue
        ref = _REF.search(rest)
        col = DocColumn(name=name, type=rest.split(" REFERENCES")[0].split(" DEFAULT")[0].split(" NOT NULL")[0]
                        .split(" PRIMARY KEY")[0].split(" UNIQUE")[0].strip(),
                        comment=comment if i == len(defs) - 1 else "",
                        references=ref.group(1) if ref else None,
                        deprecated="deprecated" in comment.lower() or "do not implement" in comment.lower())
        table.columns.setdefault(name, col)
        if ref:
            table.fks.append((name, ref.group(1)))


@lru_cache(maxsize=1)
def _cached(mtime: float) -> dict[str, DocTable]:
    return parse_spec(SPEC_PATH.read_text(encoding="utf-8"))


def load_spec_tables() -> dict[str, DocTable]:
    if not SPEC_PATH.exists():
        return {}
    return _cached(SPEC_PATH.stat().st_mtime)
