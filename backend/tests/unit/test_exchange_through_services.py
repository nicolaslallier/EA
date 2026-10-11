"""An import writes through the services, never through a repository (docs/adr/0048, 0050).

Packages and views are written by `PackageService` and `DiagramService`, in the import's
shared transaction, so that the audit trail, the caller's role, the tree's lock and the rules
of the tree apply exactly as for a person. A repository reached from the exchange modules would
write a graph the services would have refused and an audit trail with a hole. This reads the
source, like `test_audit_coverage.py`: the exchange modules import nothing of `ea.repositories`
or `ea.db`, and reach into no other object's `_repository`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SERVICES = Path(__file__).parents[2] / "src" / "ea" / "services"
MODULES = ["exchange_import.py", "exchanges.py", "exchange_admission.py"]
FORBIDDEN_PREFIXES = ("ea.repositories", "ea.db")


def reaches_below_the_services(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(FORBIDDEN_PREFIXES):
            found.append(f"line {node.lineno}: from {node.module} import ...")
        elif isinstance(node, ast.Import):
            found.extend(
                f"line {node.lineno}: import {a.name}"
                for a in node.names
                if a.name.startswith(FORBIDDEN_PREFIXES)
            )
        elif (
            isinstance(node, ast.Attribute)
            and node.attr in {"_repository", "_sessions", "_store"}
            and ast.unparse(node.value) != "self"
        ):
            found.append(f"line {node.lineno}: {ast.unparse(node)}")
    return found


@pytest.mark.parametrize("module", MODULES)
def test_the_exchange_modules_reach_no_repository_but_their_own_port(module: str) -> None:
    assert reaches_below_the_services((SERVICES / module).read_text()) == []


def test_the_scan_sees_an_import_of_a_repository_and_a_reach_into_another_service() -> None:
    imports = "from ea.repositories.package_store import PostgresPackageRepository\n"
    reach = "async def f(packages):\n    await packages._repository.add(p, audit=())\n"
    clean = "from ea.domain.exchange import Issue\nself._repository.apply_rows(rows, apply)\n"

    assert reaches_below_the_services(imports)
    assert reaches_below_the_services("import ea.db.unit_of_work\n")
    assert reaches_below_the_services(reach) == ["line 2: packages._repository"]
    assert reaches_below_the_services(clean) == []
