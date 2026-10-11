"""No repository writes to an element, or to what hangs from it, without the review guard.

Like `test_audit_coverage.py`, this reads the source: a writer added to one of
these stores that does not go through the guard fails here (docs/adr/0046).
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from ea.repositories import document_store, package_store

#: store class -> {writer -> a name its body must call: the locked, in-transaction guard}
GUARDED = {
    document_store.PostgresDocumentRepository: {
        "add": "_hold_the_element",
        "replace": "_hold_the_element",
        "delete": "_hold_the_element",
    },
    package_store.PostgresPackageRepository: {
        "move_elements": "check_all_untouched",
        "delete": "check_all_untouched",
    },
}


def calls(source: str) -> set[str]:
    return {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(ast.parse(textwrap.dedent(source)))
        if isinstance(node, ast.Call)
    }


@pytest.mark.parametrize("store", list(GUARDED), ids=lambda c: c.__name__)
def test_every_writer_calls_its_guard(store: type) -> None:
    for writer, guard in GUARDED[store].items():
        assert guard in calls(inspect.getsource(getattr(store, writer))), (
            f"{store.__name__}.{writer}"
        )


def test_the_analyser_fails_on_an_unguarded_writer() -> None:
    assert "_hold_the_element" not in calls("async def add(self):\n    session.add(row)\n")
