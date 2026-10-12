"""`services/` never imports `repositories/` (api -> services -> domain <- repositories)."""

from __future__ import annotations

import ast
from pathlib import Path

SERVICES = Path(__file__).parents[2] / "src" / "ea" / "services"


def test_no_service_imports_a_repository() -> None:
    offenders = []
    for path in sorted(SERVICES.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            modules = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            offenders += [f"{path.name}: {m}" for m in modules if m.startswith("ea.repositories")]
    assert offenders == []
