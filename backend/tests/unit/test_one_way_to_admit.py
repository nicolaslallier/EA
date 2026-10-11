"""A place among the imports and exports is taken by `lease()` and by nothing else (docs/adr/0048).

Like `test_one_way_to_open_a_unit.py`, this reads the source. A place taken and given back by two
calls made by hand is a place that one path forgets to give back; the context manager gives it
back however the block ends. Outside `services/exchange_admission.py`, nothing may call a taking
or giving primitive on an admission, a lease or what holds one.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).parents[2] / "src" / "ea"
OWNER = SRC / "services" / "exchange_admission.py"
PRIMITIVES = {"admit", "acquire", "release", "_finish", "_purge", "_leave"}
HOLDERS = ("admission", "lease", "ticket", "_exports", "_imports", "places")


def primitives_called(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in PRIMITIVES
            and any(holder in ast.unparse(node.func.value).lower() for holder in HOLDERS)
        ):
            found.append(f"line {node.lineno}: {ast.unparse(node.func)}(...)")
    return found


def test_nothing_outside_the_admission_takes_or_gives_a_place_by_hand() -> None:
    offenders = {
        path.relative_to(SRC).as_posix(): primitives_called(path.read_text())
        for path in sorted(SRC.rglob("*.py"))
        if path != OWNER and primitives_called(path.read_text())
    }

    assert offenders == {}


def test_the_scan_sees_a_primitive_called_by_hand() -> None:
    assert primitives_called("ticket = self._exports.admit('a')\n") != []
    assert primitives_called("lease.release()\n") != []
    assert primitives_called("admission._finish(lease)\n") != []
    assert primitives_called("async with admission.lease(key) as lease:\n    pass\n") == []
    assert primitives_called("gate.slot.release()\n") == []  # a semaphore is not a place
