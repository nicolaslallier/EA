"""The nine guarded tables are listed twice on purpose; this keeps the two lists equal."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from ea.repositories.frozen_tables import GUARDED_TABLES, lock_guarded


def test_the_stores_list_is_the_migrations_list() -> None:
    path = next((Path(__file__).parents[2] / "migrations" / "versions").glob("0020_*.py"))
    spec = importlib.util.spec_from_file_location("migration_0020", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert tuple(table for table, _ in module._GUARDED) == GUARDED_TABLES


def test_the_lock_statement_names_every_table_in_the_mode_asked() -> None:
    statement = lock_guarded("ROW EXCLUSIVE")
    assert statement.startswith("LOCK TABLE elements, ")
    assert statement.endswith(" IN ROW EXCLUSIVE MODE")
    assert all(table in statement for table in GUARDED_TABLES)
