"""The guarded tables are listed twice on purpose; this keeps the two lists equal.

The migrations' list is `0020`'s nine, then `0022`'s external references (docs/adr/0076).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from ea.repositories.frozen_tables import GUARDED_TABLES, lock_guarded


def _migration(revision: str) -> Any:
    path = next((Path(__file__).parents[2] / "migrations" / "versions").glob(f"{revision}_*.py"))
    spec = importlib.util.spec_from_file_location(f"migration_{revision}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_stores_list_is_the_migrations_list() -> None:
    listed = (*_migration("0020")._GUARDED, *_migration("0022")._GUARDED)
    assert tuple(table for table, _ in listed) == GUARDED_TABLES


def test_a_reference_is_frozen_on_what_it_is_never_on_its_cached_copy() -> None:
    module = _migration("0022")
    assert set(module._IDENTITY) == {"element_id", "kind", "target", "external_id", "url"}
    assert not {"title", "status", "synced_at", "attempted_at"} & set(module._IDENTITY)


def test_the_lock_statement_names_every_table_in_the_mode_asked() -> None:
    statement = lock_guarded("ROW EXCLUSIVE")
    assert statement.startswith("LOCK TABLE elements, ")
    assert statement.endswith(" IN ROW EXCLUSIVE MODE")
    assert all(table in statement for table in GUARDED_TABLES)
