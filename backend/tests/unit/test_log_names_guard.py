"""A name that came from a person never reaches a log line unwrapped (docs/adr/0048).

While a file is imported, `logged(name)` shows `<imported>` instead of the name: the file is
untrusted (a 5 MB name, a forged `[ERROR]` line, someone's secret), and the audit trail keeps
the names anyway. A service that quotes a name, a title, a file name or a description in a log
call without `logged(...)` leaks it the day an import reaches it. This reads the source, like
`test_service_guards.py`, and fails on any such call.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SERVICES = Path(__file__).parents[2] / "src" / "ea" / "services"
LEVELS = {"debug", "info", "warning", "error", "exception", "critical"}
#: Attributes (or variables) that hold what a person typed.
TEXTY = {"name", "title", "filename", "path", "description", "label", "documentation"}
#: `file:attribute` that are not a person's text, each with its reason. Nothing else.
JUSTIFIED: dict[str, str] = {}


def unwrapped(source: str) -> list[tuple[int, str]]:
    """`(line, expression)` of every texty value of a `logger.<level>(...)` outside `logged()`."""
    found: list[tuple[int, str]] = []
    tree = ast.parse(source)

    def walk(node: ast.AST) -> list[ast.expr]:
        bad: list[ast.expr] = []
        if isinstance(node, ast.Call) and ast.unparse(node.func).split(".")[-1] == "logged":
            return bad
        if (isinstance(node, ast.Attribute) and node.attr in TEXTY) or (
            isinstance(node, ast.Name) and node.id in TEXTY
        ):
            bad.append(node)
        for child in ast.iter_child_nodes(node):
            bad.extend(walk(child))
        return bad

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in LEVELS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in {"logger", "log"}
        ):
            for argument in [*node.args, *(k.value for k in node.keywords)]:
                found.extend((e.lineno, ast.unparse(e)) for e in walk(argument))
    return sorted(found)


@pytest.mark.parametrize("path", sorted(SERVICES.glob("*.py")), ids=lambda p: p.name)
def test_no_service_logs_a_name_without_logged(path: Path) -> None:
    leaks = [
        (line, text)
        for line, text in unwrapped(path.read_text())
        if f"{path.name}:{text}" not in JUSTIFIED
    ]

    assert leaks == [], f"{path.name} logs what a person typed without logged(): {leaks}"


def test_the_scan_sees_a_leak_and_a_wrapped_name() -> None:
    leaky = "logger.info('made %s', diagram.name, extra={'title': stored.title})"
    wrapped = "logger.info('made %s', logged(diagram.name), extra={'n': logged(stored.title)})"
    plain = "logger.info('made', extra={'action': 'x', 'count': len(items)})"

    assert [text for _, text in unwrapped(leaky)] == ["diagram.name", "stored.title"]
    assert unwrapped(wrapped) == [] and unwrapped(plain) == []
    assert unwrapped("print(diagram.name)") == []  # not a log call
