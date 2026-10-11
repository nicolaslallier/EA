"""`scripts/npm_audit.py` : une alerte n'est tolérée que nommée, high, et non expirée."""

from __future__ import annotations

import datetime
import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location("npm_audit", REPO_ROOT / "scripts" / "npm_audit.py")
assert _SPEC is not None and _SPEC.loader is not None
npm_audit: ModuleType = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(npm_audit)

TODAY = datetime.date(2026, 10, 11)
ADVISORY = "GHSA-vfj7-8cjw-p6xm"
OTHER = "GHSA-aaaa-bbbb-cccc"


def an_advisory(ident: str = ADVISORY, severity: str = "high") -> dict[str, object]:
    return {
        "source": 1,
        "title": "t",
        "url": f"https://github.com/advisories/{ident}",
        "severity": severity,
    }


def a_report(*vias: object, name: str = "braces") -> dict[str, object]:
    return {"vulnerabilities": {name: {"via": list(vias)}, "micromatch": {"via": ["braces"]}}}


def an_exception(ident: str = ADVISORY, expires: str = "2027-01-09") -> dict[str, str]:
    return {"advisory": ident, "expires": expires}


def test_a_named_high_advisory_is_tolerated() -> None:
    code, lines = npm_audit.check(a_report(an_advisory()), [an_exception()], TODAY)
    assert code == 0
    assert "toléré" in lines[0]


def test_an_expired_exception_covers_nothing() -> None:
    code, lines = npm_audit.check(
        a_report(an_advisory()), [an_exception(expires="2026-10-10")], TODAY
    )
    assert code == 1
    assert "expirée le 2026-10-10" in lines[0]


def test_the_last_day_still_counts() -> None:
    code, _ = npm_audit.check(a_report(an_advisory()), [an_exception(expires="2026-10-11")], TODAY)
    assert code == 0


def test_a_new_high_advisory_fails() -> None:
    code, lines = npm_audit.check(a_report(an_advisory(OTHER)), [an_exception()], TODAY)
    assert code == 1
    assert OTHER in lines[0] and "braces" in lines[0]


def test_a_critical_advisory_is_never_covered() -> None:
    code, lines = npm_audit.check(
        a_report(an_advisory(severity="critical")), [an_exception()], TODAY
    )
    assert code == 1
    assert "critical" in lines[0]


def test_one_unlisted_advisory_among_two_fails() -> None:
    code, _ = npm_audit.check(a_report(an_advisory(), an_advisory(OTHER)), [an_exception()], TODAY)
    assert code == 1


def test_a_moderate_advisory_is_not_blocking() -> None:
    code, lines = npm_audit.check(a_report(an_advisory(OTHER, "moderate")), [], TODAY)
    assert (code, lines) == (0, [])


def test_a_stale_exception_is_flagged_without_failing() -> None:
    code, lines = npm_audit.check({"vulnerabilities": {}}, [an_exception()], TODAY)
    assert code == 0
    assert "à supprimer" in lines[0]


def _files(tmp_path: Path) -> list[str]:
    path = tmp_path / "exceptions.json"
    path.write_text(json.dumps({"exceptions": [an_exception()]}), encoding="utf-8")
    return ["npm_audit.py", str(tmp_path), str(path)]


def _runner(stdout: str, returncode: int = 1) -> object:
    def run(_: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], returncode, stdout, "boom")

    return run


def _today() -> datetime.date:
    return TODAY


def test_main_passes_on_the_covered_report(tmp_path: Path) -> None:
    out = _runner(json.dumps(a_report(an_advisory())))
    assert npm_audit.main(_files(tmp_path), out, _today) == 0


def test_main_fails_on_invalid_json(tmp_path: Path) -> None:
    assert npm_audit.main(_files(tmp_path), _runner("pas du json"), _today) != 0


def test_main_fails_on_an_npm_error_code(tmp_path: Path) -> None:
    assert npm_audit.main(_files(tmp_path), _runner("{}", returncode=2), _today) != 0


def test_main_fails_when_npm_is_missing(tmp_path: Path) -> None:
    def missing(_: str) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("npm")

    assert npm_audit.main(_files(tmp_path), missing, _today) != 0


def test_main_fails_without_the_exceptions_file(tmp_path: Path) -> None:
    argv = ["npm_audit.py", str(tmp_path), str(tmp_path / "absent.json")]
    assert npm_audit.main(argv, _runner("{}"), _today) != 0


def test_main_needs_two_arguments() -> None:
    assert npm_audit.main(["npm_audit.py"], _runner("{}"), _today) == 2


@pytest.mark.parametrize("returncode", [0, 1])
def test_main_accepts_npm_codes_zero_and_one(tmp_path: Path, returncode: int) -> None:
    out = _runner(json.dumps({"vulnerabilities": {}}), returncode)
    assert npm_audit.main(_files(tmp_path), out, _today) == 0
