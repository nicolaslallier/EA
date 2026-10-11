"""A name that came from a person never reaches a log line unwrapped (docs/adr/0050).

While a file is imported, `logged(name)` shows `<imported>` instead of the name: the file is
untrusted (a 5 MB name, a forged `[ERROR]` line, someone's secret), and the audit trail keeps
the names anyway. A service that quotes a name, a title, a file name or a description in a log
call without `logged(...)` leaks it the day an import reaches it. This reads the source, like
`test_service_guards.py`, and fails on any such call.

What it follows: every log call whatever its receiver (`logger`, `log`, `self._log`,
`logging.getLogger(...)`, `.log(level, ...)`, `.exception`), every argument and every `extra=`
value; a name taken from a variable assigned in the same function; `str(...)` and `repr(...)` of
a person's value or of a caught exception (whose text quotes the value it refused); f-strings.
Limits: syntax, not types — a value reaching the call through a second function is beyond it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SERVICES = Path(__file__).parents[2] / "src" / "ea" / "services"
LEVELS = {"debug", "info", "warning", "error", "exception", "critical", "log"}
#: Attributes (or variables) that hold what a person typed.
TEXTY = {
    "name",
    "title",
    "filename",
    "path",
    "description",
    "label",
    "documentation",
    "key",
    "summary",
    "text",
    "content",
    "body",
    "comment",
}
#: Names an exception is usually caught as: `str(error)` quotes the value it refused.
CAUGHT = {"error", "exc", "exception", "refusal", "err", "e", "again", "failure"}
RECEIVER = re.compile(r"\A(self\.)?_?(logger|log)\Z|\Alogging\.getLogger\(.*\)\Z")
#: `file:expression` that are not a person's text, each with its reason. Nothing else.
JUSTIFIED: dict[str, str] = {}


def _texty(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr in TEXTY) or (
        isinstance(node, ast.Name) and node.id in TEXTY
    )


def _leaks(node: ast.AST, tainted: set[str]) -> list[ast.expr]:
    """The sub-expressions of `node` that carry a person's text, outside any `logged(...)`."""
    if isinstance(node, ast.Call):
        callee = ast.unparse(node.func).split(".")[-1]
        if callee in {"logged", "len"}:
            return []  # a length says nothing of what was typed
        if callee in {"str", "repr"} and node.args:
            first = node.args[0]
            if isinstance(first, ast.Name) and first.id in CAUGHT:
                return [node]
    found: list[ast.expr] = []
    if isinstance(node, ast.Attribute):
        if _texty(node):
            return [node]
        if isinstance(node.value, ast.Name):
            return []  # `content.size`: an attribute of an object, not the object's text
    if _texty(node) or (isinstance(node, ast.Name) and node.id in tainted):
        found.append(node)  # type: ignore[arg-type]
    for child in ast.iter_child_nodes(node):
        found.extend(_leaks(child, tainted))
    return found


#: Calls that hand the text back (changed, not replaced by something else).
TEXT_PRESERVING = {"str", "repr", "strip", "lower", "upper", "casefold", "format", "join"}


def _copies_text(value: ast.expr, tainted: set[str]) -> bool:
    """Whether the assigned value *is* a person's text, not merely built from some of it."""
    if isinstance(value, ast.Await):
        return False  # the result of a use case is an object, not the text passed to it
    if isinstance(value, ast.Call):
        return ast.unparse(value.func).split(".")[-1] in TEXT_PRESERVING and bool(
            _leaks(value, tainted)
        )
    return bool(_leaks(value, tainted))


def _tainted_in(function: ast.AST) -> set[str]:
    """Variables a person's text is copied into, in one function (`n = d.name`, `t = str(e)`)."""
    tainted: set[str] = set()
    changed = True
    while changed:  # a copy of a copy
        changed = False
        for node in ast.walk(function):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if (
                    isinstance(target, ast.Name)
                    and target.id not in tainted
                    and _copies_text(node.value, tainted)
                ):
                    tainted.add(target.id)
                    changed = True
    return tainted


def unwrapped(source: str) -> list[tuple[int, str]]:
    """`(line, expression)` of every texty value of a log call outside `logged()`."""
    found: list[tuple[int, str]] = []
    tree = ast.parse(source)
    scopes: list[ast.AST] = [
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    ] or [tree]
    for scope in scopes:
        tainted = _tainted_in(scope)
        for node in ast.walk(scope):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in LEVELS
                and RECEIVER.match(ast.unparse(node.func.value))
            ):
                continue
            arguments = [*node.args, *(k.value for k in node.keywords)]
            if node.func.attr == "log":
                arguments = arguments[1:]  # the first is the level
            for argument in arguments:
                found.extend((e.lineno, ast.unparse(e)) for e in _leaks(argument, tainted))
    return sorted(set(found))


@pytest.mark.parametrize("path", sorted(SERVICES.glob("*.py")), ids=lambda p: p.name)
def test_no_service_logs_a_name_without_logged(path: Path) -> None:
    leaks = [
        (line, text)
        for line, text in unwrapped(path.read_text())
        if f"{path.name}:{text}" not in JUSTIFIED
    ]

    assert leaks == [], f"{path.name} logs what a person typed without logged(): {leaks}"


def seen(source: str) -> list[str]:
    return [text for _, text in unwrapped(source)]


def test_the_scan_sees_a_leak_and_a_wrapped_name() -> None:
    assert seen("logger.info('x %d', len(stored.content))") == []
    leaky = "logger.info('made %s', diagram.name, extra={'title': stored.title})"
    wrapped = "logger.info('made %s', logged(diagram.name), extra={'n': logged(stored.title)})"
    plain = "logger.info('made', extra={'action': 'x', 'count': len(items)})"

    assert seen(leaky) == ["diagram.name", "stored.title"]
    assert seen(wrapped) == [] and seen(plain) == []
    assert seen("print(diagram.name)") == []  # not a log call


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def f(d):\n    n = d.name\n    logger.info('x %s', n)\n", ["n"]),
        ("def f(d):\n    n = d.name\n    m = n\n    logger.info('x', extra={'a': m})\n", ["m"]),
        ("def f(d):\n    n = logged(d.name)\n    logger.info('x %s', n)\n", []),
        ("logging.getLogger('ea').info('x %s', d.name)\n", ["d.name"]),
        ("class S:\n    def f(self, d):\n        self._log.warning('x %s', d.name)\n", ["d.name"]),
        ("logger.log(logging.INFO, 'x %s', d.key)\n", ["d.key"]),
        ("logger.log(logging.INFO, 'x')\n", []),
        ("logger.exception('failed %s', stored.summary)\n", ["stored.summary"]),
        (
            "def f():\n    try:\n        g()\n    except ValueError as error:\n"
            "        logger.warning('refused: %s', str(error))\n",
            ["str(error)"],
        ),
        ("def f(d):\n    logger.info(f'made {d.name} now')\n", ["d.name"]),
        ("def f(d):\n    logger.info('x %s', repr(d.name))\n", ["d.name"]),
        ("def f(d):\n    logger.info('x %s', str(d.id))\n", []),
    ],
)
def test_the_scan_follows_variables_receivers_and_exception_texts(
    source: str, expected: list[str]
) -> None:
    assert seen(source) == expected
