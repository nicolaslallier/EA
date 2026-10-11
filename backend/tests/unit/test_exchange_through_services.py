"""An import writes through the services, never through a repository (docs/adr/0050, 0052).

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
FORBIDDEN_PREFIXES = ("ea.repositories", "ea.db", "sqlalchemy", "asyncpg")
#: Private attributes that are a store or a session, wherever they are reached from.
PRIVATE_STORES = {"_repository", "_sessions", "_store", "_session", "_engine"}
#: `module: reason` — a module of the exchange allowed to reach below the services. None today:
#: the repository of an exchange is `repositories/exchange_store.py`, which is not scanned.
JUSTIFIED: dict[str, str] = {}


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
            and node.attr in PRIVATE_STORES
            and ast.unparse(node.value) != "self"
        ) or (
            isinstance(node, ast.Call)
            and ast.unparse(node.func) in {"getattr", "vars", "object.__getattribute__"}
            and any(
                isinstance(a, ast.Constant) and a.value in PRIVATE_STORES for a in node.args[1:2]
            )
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
    sql = "from sqlalchemy import text\n"
    sneaky = 'x = getattr(packages, "_repository")\n'

    assert reaches_below_the_services(imports)
    assert reaches_below_the_services("import ea.db.unit_of_work\n")
    assert reaches_below_the_services(reach) == ["line 2: packages._repository"]
    assert reaches_below_the_services(clean) == []
    assert reaches_below_the_services(sql) == ["line 1: from sqlalchemy import ..."]
    assert reaches_below_the_services("import sqlalchemy.orm\n")
    assert reaches_below_the_services("from asyncpg import Connection\n")
    assert reaches_below_the_services(sneaky) == ["line 1: getattr(packages, '_repository')"]
    assert reaches_below_the_services('getattr(self, "_repository")\n') != []  # even on self
