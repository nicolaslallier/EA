"""A repository gets its session from `ea.db.unit_of_work`, and from nowhere else (docs/adr/0052).

Like `test_audit_coverage.py`, this reads the source. A store that opened
`self._sessions.begin()` itself would, in the middle of an import, write
*outside* the import's transaction: its rows would survive a dry run and a
refused row. The analyser takes source text, so the last test can show it
failing on a made-up store.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPOSITORIES = Path(__file__).parents[2] / "src" / "ea" / "repositories"

#: Modules whose sessions are not the model's, and why they may open their own.
OWN_SESSIONS = {
    "audit_store.py": "reads the trail from a request of its own; an import never queries it",
    "exchange_store.py": "streams a consistent snapshot of the whole graph for the export",
    "file_metadata_store.py": "what is known about bucket objects: not the model (docs/adr/0039)",
}


def direct_sessions(source: str) -> list[str]:
    """Every `self._sessions(...)` or `self._sessions.<anything>(...)` call in `source`."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        owner = callee.value if isinstance(callee, ast.Attribute) else callee
        if "self._sessions" in {ast.unparse(callee), ast.unparse(owner)}:
            found.append(f"line {node.lineno}: {ast.unparse(node.func)}(...)")
    return found


def test_no_converted_store_opens_a_session_of_its_own() -> None:
    offenders = {
        path.name: direct_sessions(path.read_text())
        for path in sorted(REPOSITORIES.glob("*.py"))
        if path.name not in OWN_SESSIONS and direct_sessions(path.read_text())
    }
    assert offenders == {}


def test_the_excused_modules_still_exist_and_still_open_their_own() -> None:
    """An excuse that no longer applies is dropped, not kept."""
    for name in OWN_SESSIONS:
        assert direct_sessions((REPOSITORIES / name).read_text()), name


def test_the_converted_stores_really_go_through_the_unit_of_work() -> None:
    for name in (
        "architecture_store.py",
        "document_store.py",
        "diagram_store.py",
        "package_store.py",
        "saved_query_store.py",
        "baseline_store.py",
    ):
        source = (REPOSITORIES / name).read_text()
        assert "from ea.db.unit_of_work import" in source, name


def test_the_analyser_fails_on_a_store_that_opens_its_own_unit() -> None:
    source = (
        "class Store:\n"
        "    async def add(self):\n"
        "        async with self._sessions.begin() as session:\n"
        "            session.add(row)\n"
        "    async def get(self):\n"
        "        async with self._sessions() as session:\n"
        "            return await session.get(Row, 1)\n"
        "    async def fine(self):\n"
        "        async with work(self._sessions) as session:\n"
        "            session.add(row)\n"
    )
    assert direct_sessions(source) == [
        "line 3: self._sessions.begin(...)",
        "line 6: self._sessions(...)",
    ]
