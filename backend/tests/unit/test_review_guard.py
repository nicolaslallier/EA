"""Every way into an element asks the same rule, in the transaction that writes (docs/adr/0048).

Like `test_audit_coverage.py`, this reads the source. The rule is ONE function,
`guard_element_write`; a route that stops calling it — or a new route that never
did — fails here. The two routes that cannot call a Python function on a row
(the restoration's protection is a set read in SQL) are tied to it by a
parametrised parity test against PostgreSQL, in
`tests/integration/test_element_review.py`.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

from ea.domain import approval
from ea.repositories import document_store, package_store
from ea.services.architecture import ArchitectureService

#: (where, route) -> a name the route's body must call.
ROUTES = {
    (
        "ArchitectureService.update_element",
        ArchitectureService.update_element,
    ): "guard_element_write",
    (
        "ArchitectureService.delete_element",
        ArchitectureService.delete_element,
    ): "guard_element_write",
    ("ArchitectureService.connect/disconnect", ArchitectureService._review_of_ends): (
        "guard_element_write"
    ),
    ("document_store._guard_the_element", document_store._guard_the_element): "guard_element_write",
    (
        "PostgresDocumentRepository.add",
        document_store.PostgresDocumentRepository.add,
    ): "_guard_the_element",
    (
        "PostgresDocumentRepository.replace",
        document_store.PostgresDocumentRepository.replace,
    ): "_guard_the_element",
    (
        "PostgresDocumentRepository.delete",
        document_store.PostgresDocumentRepository.delete,
    ): "_guard_the_element",
    (
        "PostgresPackageRepository.move_elements",
        package_store.PostgresPackageRepository.move_elements,
    ): "guard_all",
    (
        "PostgresPackageRepository.delete",
        package_store.PostgresPackageRepository.delete,
    ): "guard_all",
    ("approval.guard_all", approval.guard_all): "guard_element_write",
}


def calls(source: str) -> set[str]:
    return {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(ast.parse(textwrap.dedent(source)))
        if isinstance(node, ast.Call)
    }


@pytest.mark.parametrize(
    ("where", "route"), list(ROUTES), ids=lambda r: r if isinstance(r, str) else ""
)
def test_every_route_asks_the_one_rule(where: str, route: object) -> None:
    assert ROUTES[(where, route)] in calls(inspect.getsource(route)), where  # type: ignore[arg-type]


def test_the_analyser_fails_on_a_route_that_skips_the_rule() -> None:
    assert "guard_element_write" not in calls("async def add(self):\n    session.add(row)\n")


def test_nothing_else_decides_by_the_review_state() -> None:
    """No copy of the rule: the states are only compared in the domain's rule and its SQL twin."""
    from pathlib import Path

    src = Path(__file__).parents[2] / "src" / "ea"
    allowed = {
        "domain/approval.py",
        "domain/audit.py",  # a view of the state for the journal
        "repositories/architecture_store.py",  # the SQL twin of the rule, and the mapping
        "api/schemas.py",  # what is shown
    }
    offenders = [
        str(path.relative_to(src))
        for path in src.rglob("*.py")
        if str(path.relative_to(src)) not in allowed
        and ("ApprovalState.APPROVED" in path.read_text() or "is_frozen(" in path.read_text())
    ]
    assert offenders == []


# --- No new door into the `elements` table ------------------------------------------------

WRITING_CALLS = {
    "update",
    "delete",
    "insert",
    "add",
    "add_all",
    "merge",
    "setattr",
    "ElementRecord",
}

#: Every function allowed to write to `elements` — nothing else may — and what guards it.
ELEMENT_WRITERS = {
    "architecture_store.py": {
        "PostgresArchitectureRepository.add_element": "creates the row",
        "PostgresArchitectureRepository._change": "update_element / transition_element: the "
        "service's patch calls guard_element_write on the row it holds locked",
        "PostgresArchitectureRepository.delete_element": "guard= and ends= (guard_element_write)",
        "_write_review": "writes review columns of rows locked by _review_rows / _change",
        "touch": "documents (guarded by _guard_the_element); callers hold the rows locked",
    },
    "baseline_store.py": {
        "_write": "plan_restore blocks on approved_or_locked, read in the same transaction",
        "PostgresBaselineRepository.create": "copies FROM elements into baseline_*: no write to it",
    },
    "package_store.py": {
        "PostgresPackageRepository.delete": "guard_all(FILING)",
        "PostgresPackageRepository.move_elements": "guard_all(FILING)",
    },
}
#: Outside `repositories/`: the one-off import, deleted after the cut-over (docs/adr/0033).
SCRIPT_WRITERS = {"graph_import.py"}


def _functions(source: str) -> dict[str, ast.AST]:
    """Top-level functions and methods, by qualified name (nested defs belong to their parent)."""
    found: dict[str, ast.AST] = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            found[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                    found[f"{node.name}.{member.name}"] = member
    return found


def _callee(call: ast.Call) -> str:
    return call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", "")


def element_writers(source: str) -> set[str]:
    """Functions of `source` that mention `ElementRecord` and write: update/delete/insert it,
    construct it, add or merge a row, or `setattr` on one."""
    writers = set()
    for name, function in _functions(source).items():
        nodes = list(ast.walk(function))
        mentions = any(isinstance(n, ast.Name) and n.id == "ElementRecord" for n in nodes)
        writes = any(isinstance(n, ast.Call) and _callee(n) in WRITING_CALLS for n in nodes)
        if mentions and writes:
            writers.add(name)
    return writers


def _writes_column(function: ast.AST, column: str) -> bool:
    """Whether the function assigns `column`: `row.column = `, `d["column"] = `, a `values(column=)`
    keyword or a `{"column": ...}` key."""
    for n in ast.walk(function):
        if isinstance(n, ast.Attribute) and n.attr == column and isinstance(n.ctx, ast.Store):
            return True
        if (
            isinstance(n, ast.Subscript)
            and isinstance(n.ctx, ast.Store)
            and isinstance(n.slice, ast.Constant)
            and n.slice.value == column
        ):
            return True
        if (
            isinstance(n, ast.Call)
            and _callee(n) == "values"
            and any(k.arg == column for k in n.keywords)
        ):
            return True
        if isinstance(n, ast.Dict) and any(
            isinstance(k, ast.Constant) and k.value == column for k in n.keys
        ):
            return True
    return False


def _mentions(function: ast.AST, name: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(function))


def revision_without_author(source: str) -> set[str]:
    """Functions that write `revision` and not `last_modified_by_sub`, or the reverse.

    The `revision` of an element is the one that names an author. A diagram has a
    counter of the same name (docs/adr/0063) that only guards a concurrent save, so a
    function that handles a `DiagramRecord` is not about an element's revision.
    """
    return {
        name
        for name, function in _functions(source).items()
        if not _mentions(function, "DiagramRecord")
        and _writes_column(function, "revision") != _writes_column(function, "last_modified_by_sub")
    }


NEW_DOOR = """
class Sneaky:
    async def rename_everything(self, session):
        await session.execute(update(ElementRecord).values(name="x"))
"""
HALF_A_TOUCH = """
def bump(row):
    row.revision = row.revision + 1


async def author_only(session):
    await session.execute(update(Other).values(last_modified_by_sub="x"))
"""


DIAGRAM_COUNTER = """
async def save(session):
    row = await session.get(DiagramRecord, 1)
    row.revision = row.revision + 1
"""


def _src() -> Path:
    return Path(__file__).parents[2] / "src" / "ea"


def test_the_analyser_sees_a_new_door_and_a_half_touch() -> None:
    assert element_writers(NEW_DOOR) == {"Sneaky.rename_everything"}
    assert element_writers("def read(row: ElementRecord):\n    return row.name\n") == set()
    assert revision_without_author(HALF_A_TOUCH) == {"bump", "author_only"}
    assert revision_without_author(DIAGRAM_COUNTER) == set()


def test_only_the_declared_functions_write_to_the_elements_table() -> None:
    found = {
        path.name: element_writers(path.read_text())
        for path in (_src() / "repositories").glob("*.py")
    }
    assert {name: writers for name, writers in found.items() if writers} == {
        module: set(declared) for module, declared in ELEMENT_WRITERS.items()
    }
    for script in SCRIPT_WRITERS:
        assert (_src() / script).exists(), f"{script} is gone: drop it from the list"


def test_every_write_of_a_revision_names_its_author_and_the_reverse() -> None:
    offenders = {
        str(path.relative_to(_src())): revision_without_author(path.read_text())
        for path in _src().rglob("*.py")
        if revision_without_author(path.read_text())
    }
    assert offenders == {}
