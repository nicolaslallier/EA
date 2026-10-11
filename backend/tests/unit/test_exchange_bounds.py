"""A bound that is declared is a bound that is applied (docs/adr/0048).

A ceiling nobody reads is worse than none: it documents a protection that is not
there. Every `MAX_*` / `*_LIMIT` / page-size / time-budget constant of the exchange
modules must be *read* somewhere other than its own declaration. This reads the
source, like `test_service_guards.py`; the behaviour of each bound is tested
beside the code that applies it, by lowering the bound (never by building a huge file).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).parents[2] / "src" / "ea"
DECLARING = [SRC / "domain" / "exchange.py", *sorted((SRC / "formats").glob("*.py"))]
BOUND = re.compile(r"\A(MAX_\w+|\w+_LIMIT|\w+_PAGE_SIZE|\w+_TIMEOUT_SECONDS)\Z")


def declared_bounds(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    names = []
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
        elif isinstance(node, ast.Assign):
            names.extend(t.id for t in node.targets if isinstance(t, ast.Name))
    return [name for name in names if BOUND.match(name)]


def _reads(node: ast.AST, name: str) -> bool:
    if isinstance(node, ast.Name):
        return node.id == name and isinstance(node.ctx, ast.Load)
    return isinstance(node, ast.Attribute) and node.attr == name


def reads_of(name: str) -> int:
    """Places in `src/ea` that read `name` — an import or the declaration itself is not one."""
    trees = (ast.parse(path.read_text()) for path in SRC.rglob("*.py"))
    return sum(_reads(node, name) for tree in trees for node in ast.walk(tree))


def test_the_exchange_modules_declare_bounds() -> None:
    assert sum(len(declared_bounds(path)) for path in DECLARING) >= 8  # the scan is not blind


@pytest.mark.parametrize(
    ("path", "name"),
    [(path, name) for path in DECLARING for name in declared_bounds(path)],
    ids=lambda value: value.name if isinstance(value, Path) else value,
)
def test_every_declared_bound_is_read_somewhere(path: Path, name: str) -> None:
    assert reads_of(name) > 0, f"{path.name}: {name} is declared and never applied"


def test_the_scan_sees_an_unapplied_bound() -> None:
    module = ast.parse("MAX_THING: Final = 3\nvalue = 1\n")
    names = [
        n.target.id
        for n in module.body
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
    ]
    assert names == ["MAX_THING"] and reads_of("MAX_NEVER_DECLARED_ANYWHERE") == 0
