"""Every way into an element asks the same rule, in the transaction that writes (docs/adr/0046).

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
    ("document_store._hold_the_element", document_store._hold_the_element): "guard_element_write",
    (
        "PostgresDocumentRepository.add",
        document_store.PostgresDocumentRepository.add,
    ): "_hold_the_element",
    (
        "PostgresDocumentRepository.replace",
        document_store.PostgresDocumentRepository.replace,
    ): "_hold_the_element",
    (
        "PostgresDocumentRepository.delete",
        document_store.PostgresDocumentRepository.delete,
    ): "_hold_the_element",
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
