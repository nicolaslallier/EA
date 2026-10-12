"""The tables whose rows a published subtree freezes (docs/adr/0074).

The migration `0020` keeps its own literal list, on purpose (a migration never imports the
code it froze); `tests/unit/test_frozen_tables.py` keeps the two equal. A publication locks
all of them in SHARE mode and a restoration in ROW EXCLUSIVE mode, so the two exclude each
other before either one reads anything.
"""

from __future__ import annotations

from typing import Final

GUARDED_TABLES: Final = (
    "elements",
    "relationships",
    "element_documents",
    "element_stereotypes",
    "diagrams",
    "diagram_nodes",
    "diagram_objects",
    "diagram_edges",
    "packages",
)


def lock_guarded(mode: str) -> str:
    """`LOCK TABLE` over every guarded table; `mode` is one of our own constants."""
    return "LOCK TABLE " + ", ".join(GUARDED_TABLES) + f" IN {mode} MODE"
