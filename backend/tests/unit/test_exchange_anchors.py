"""A validation pattern of the exchange modules accepts the whole string or nothing.

`$` matches before a trailing newline, so `"abc\\n"` would pass an NCName check
and put a line break into an `xs:ID`. Every pattern is anchored with `\\A…\\Z`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from ea.domain.exchange import xml_identifier

SRC = Path(__file__).parents[2] / "src" / "ea"
NCNAME = re.compile(r"\A[A-Za-z_][A-Za-z0-9_.-]*\Z")
DIRTY = ["x\n", "x\r\n", "x\x00", " x", "x ", "\nx", "x" + chr(0x2028)]


@pytest.mark.parametrize("raw", DIRTY)
def test_an_identifier_with_stray_whitespace_is_still_normalised(raw: str) -> None:
    assert NCNAME.match(xml_identifier(raw))


def exchange_modules() -> list[Path]:
    return [
        SRC / "domain" / "exchange.py",
        *sorted((SRC / "formats").glob("*.py")),
        SRC / "services" / "exchanges.py",
        SRC / "services" / "exchange_import.py",
    ]


def anchored_patterns(path: Path) -> list[str]:
    """Every literal passed to `re.compile` that is meant to validate a whole string."""
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if (
            isinstance(node, ast.Call)
            and ast.unparse(node.func) == "re.compile"
            and isinstance(node.args[0], ast.Constant)
        ):
            found.append(str(node.args[0].value))
    return [p for p in found if p.startswith(("^", r"\A"))]


@pytest.mark.parametrize("path", exchange_modules(), ids=lambda p: p.name)
def test_no_validation_pattern_ends_with_a_dollar(path: Path) -> None:
    for pattern in anchored_patterns(path):
        assert pattern.startswith(r"\A"), f"{path.name}: {pattern!r} must start with \\A"
        assert pattern.endswith(r"\Z"), f"{path.name}: {pattern!r} must end with \\Z"
