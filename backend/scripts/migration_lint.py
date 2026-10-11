"""Expand/contract check for migrations (ADR 0008).

A release may only EXPAND the schema (new tables, new nullable or defaulted columns, new indexes). Removing or narrowing something
is a CONTRACT step: it may ship only after a release that no longer reads it, and says so with a marker line in the migration:

    # contract: <the release that stopped reading this>

Only `upgrade()` is checked (a downgrade is allowed to undo an expansion). Migrations before 0009 predate the rule.

    python scripts/migration_lint.py          # exit 1 and a list when a migration breaks the rule
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
FIRST_CHECKED = 9
CONTRACT_OPS = {"drop_column", "drop_table", "drop_constraint", "drop_index", "alter_column", "rename_table", "execute"}
MARKER = re.compile(r"^\s*#\s*contract:\s*\S+", re.M)


def _calls(node: ast.AST):
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) and n.func.value.id == "op":
            yield n


def problems(source: str, name: str = "migration") -> list[str]:
    """What is wrong with one migration's source (empty when it is expand-only or carries the marker)."""
    tree = ast.parse(source.lstrip("\ufeff"))
    up = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"), None)
    if up is None or MARKER.search(source):
        return []
    found = []
    for call in _calls(up):
        op = call.func.attr
        if op in CONTRACT_OPS:
            found.append(f"{name}: op.{op} removes or changes something; ship it a release after nothing reads it, with a "
                         "'# contract: <release>' line")
        if op == "add_column" and len(call.args) >= 2:
            col = call.args[1]
            kw = {k.arg: k.value for k in getattr(col, "keywords", [])}
            not_null = isinstance(kw.get("nullable"), ast.Constant) and kw["nullable"].value is False
            if not_null and "server_default" not in kw:
                found.append(f"{name}: add_column NOT NULL without a server_default breaks the previous release's inserts "
                             "(expand: add it nullable or with a default)")
    return found


def check_all() -> list[str]:
    out: list[str] = []
    for p in sorted(VERSIONS.glob("[0-9]*.py")):
        if int(p.name.split("_")[0]) >= FIRST_CHECKED:
            out += problems(p.read_text(encoding="utf-8"), p.name)
    return out


if __name__ == "__main__":
    bad = check_all()
    print("\n".join(bad) if bad else "migrations: expand-only (or marked) - ok")
    sys.exit(1 if bad else 0)
