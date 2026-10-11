"""No write to the model can slip past the audit trail by omission (docs/adr/0044).

Like `test_service_guards.py`, this reads the source: a repository method that
opens a write transaction (`self._sessions.begin()`) must take `audit` as a
required keyword-only parameter — on the PostgreSQL class, on the port it
implements and on the in-memory double — so leaving it out is a type error and
a runtime `TypeError`, never a silently unlogged change. The writes that are
deliberately not audited are listed here, each with its reason.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

import tests.conftest as conftest_module
from ea.domain import ports
from ea.repositories import (
    architecture_store,
    diagram_store,
    document_store,
    package_store,
    saved_query_store,
)

SRC = Path(__file__).parents[2] / "src" / "ea"

#: Postgres class -> (port, in-memory double). Every store that writes the model.
AUDITED_STORES = {
    architecture_store.PostgresArchitectureRepository: (
        ports.ArchitectureRepository,
        conftest_module.InMemoryRepository,
    ),
    document_store.PostgresDocumentRepository: (
        ports.DocumentRepository,
        conftest_module.InMemoryDocuments,
    ),
    diagram_store.PostgresDiagramRepository: (
        ports.DiagramRepository,
        conftest_module.InMemoryDiagrams,
    ),
    package_store.PostgresPackageRepository: (
        ports.PackageRepository,
        conftest_module.InMemoryPackages,
    ),
    saved_query_store.PostgresSavedQueryRepository: (
        ports.SavedQueryRepository,
        conftest_module.InMemorySavedQueries,
    ),
}

#: Writes to tables that are not the model, and why they carry no event.
NOT_AUDITED = {
    "file_metadata_store.py": (
        "`file_metadata` records what is known about a bucket object: it carries its "
        "own uploader and dates, the bucket is the source of truth for what exists, and "
        "the spec (§1) leaves MinIO files out of the trail."
    ),
}
#: Modules outside `repositories/` that write rows directly.
NOT_AUDITED_SCRIPTS = {
    "graph_import.py": "the one-off copy from Neo4j (docs/adr/0033), deleted after cut-over",
}


def _functions(cls: type) -> dict[str, ast.AsyncFunctionDef]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(cls)))
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and not node.name.startswith("_")
    }


def _writes(cls: type) -> set[str]:
    return {
        name for name, node in _functions(cls).items() if "_sessions.begin(" in ast.unparse(node)
    }


def _requires_audit(node: ast.AsyncFunctionDef) -> bool:
    names = [arg.arg for arg in node.args.kwonlyargs]
    if "audit" not in names:
        return False
    default = node.args.kw_defaults[names.index("audit")]
    return default is None  # a keyword-only parameter without a default


def test_the_expected_writes_are_found() -> None:
    """A guard that finds nothing guards nothing."""
    found = {cls.__name__: _writes(cls) for cls in AUDITED_STORES}
    assert found["PostgresArchitectureRepository"] == {
        "add_element",
        "update_element",
        "delete_element",
        "add_relationship",
        "delete_relationship",
    }
    assert found["PostgresDocumentRepository"] == {"add", "replace", "delete"}
    assert found["PostgresDiagramRepository"] == {"add", "save", "delete", "replace_layout"}
    assert found["PostgresPackageRepository"] == {"add", "save", "delete", "move_elements"}
    assert found["PostgresSavedQueryRepository"] == {"add", "save", "delete"}


@pytest.mark.parametrize("store", list(AUDITED_STORES), ids=lambda c: c.__name__)
def test_every_write_requires_an_audit_on_the_store_the_port_and_the_double(store: type) -> None:
    port, double = AUDITED_STORES[store]
    for name in _writes(store):
        for owner in (store, port, double):
            node = _functions(owner).get(name)
            assert node is not None, f"{owner.__name__}.{name} is missing"
            assert _requires_audit(node), (
                f"{owner.__name__}.{name} must take `*, audit` with no default"
            )


def test_every_store_that_opens_a_write_transaction_is_either_audited_or_excused() -> None:
    audited = {path for cls in AUDITED_STORES for path in [Path(inspect.getfile(cls)).name]}
    audit_store = "audit_store.py"  # the writer itself: it inserts events, it has none
    for path in sorted((SRC / "repositories").glob("*.py")):
        if "_sessions.begin(" not in path.read_text():
            continue
        assert path.name in audited | set(NOT_AUDITED) | {audit_store}, (
            f"{path.name} writes in a transaction: audit it or list it in NOT_AUDITED with a reason"
        )
    for name in NOT_AUDITED_SCRIPTS:
        assert (SRC / name).exists(), f"{name} no longer exists: drop it from the list"


def test_the_services_never_write_without_naming_their_audit() -> None:
    """Every call of a store write passes `audit=`, and never an anonymous `()`."""
    write_names = {name for cls in AUDITED_STORES for name in _writes(cls)}
    offenders: list[str] = []
    for path in sorted((SRC / "services").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in write_names
                and ast.unparse(node.func.value) == "self._repository"
            ):
                continue
            audit = next((k.value for k in node.keywords if k.arg == "audit"), None)
            if audit is None:
                offenders.append(f"{path.name}:{node.lineno} omits audit")
            elif isinstance(audit, ast.Tuple) and not audit.elts:
                offenders.append(f"{path.name}:{node.lineno} passes an anonymous ()")
    assert offenders == []
