"""Every write to a third party goes through the one gate, `ensure_writable` (docs/adr/0062).

Reads the source, like `test_service_guards.py`: a function of a service that calls the
provider's `create_issue` must call `ensure_writable` too, and the gate is what refuses a
read-only target before any network call.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from ea.domain.errors import UnknownExternalTargetError
from ea.services.external import ensure_writable
from tests.conftest import FakeExternalSystem

SRC = Path(__file__).parents[2] / "src" / "ea"
WRITERS = {"create_issue"}
GATES = {"ensure_writable", "_writable_system"}


def unguarded(source: str) -> list[str]:
    """Functions that call a provider write without calling the gate."""
    found: list[str] = []
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        calls = {
            c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")
            for c in ast.walk(node)
            if isinstance(c, ast.Call)
        }
        if calls & WRITERS and not calls & GATES:
            found.append(node.name)
    return found


def test_no_service_calls_a_provider_write_without_the_gate() -> None:
    offenders = {
        path.name: unguarded(path.read_text())
        for path in (SRC / "services").glob("*.py")
        if unguarded(path.read_text())
    }
    assert offenders == {}


def test_no_adapter_reaches_a_provider_write_at_all() -> None:
    for folder in ("api", "mcp"):
        for path in (SRC / folder).glob("*.py"):
            assert "create_issue" not in path.read_text(), path.name


def test_the_analyser_fails_on_a_write_that_skips_the_gate() -> None:
    sneaky = "async def go(self, system):\n    await system.create_issue('a/b', draft)\n"
    honest = (
        "async def go(self, system):\n    ensure_writable(system)\n"
        "    await system.create_issue('a/b', draft)\n"
    )
    assert unguarded(sneaky) == ["go"]
    assert unguarded(honest) == []


def test_the_gate_refuses_a_read_only_target_and_passes_a_writable_one() -> None:
    ensure_writable(FakeExternalSystem("main", writable=True))
    with pytest.raises(UnknownExternalTargetError, match="writable"):
        ensure_writable(FakeExternalSystem("main", writable=False))
