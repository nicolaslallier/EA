"""`scripts/npm_audit.py` : une alerte n'est tolérée que nommée, high, et non expirée.

Et un rapport qui n'est pas celui d'un audit — erreur de registre, objet vide,
chiffres qui ne se recoupent pas — ne passe jamais pour un audit propre.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import os
import shutil
import subprocess
from collections.abc import Callable
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

# La forme réelle d'une erreur de registre, relevée avec npm_config_registry
# pointé sur un port fermé : code de sortie 1, pas de `vulnerabilities`.
REAL_ERROR = {
    "message": "request to http://127.0.0.1:9/-/npm/v1/security/advisories/bulk failed",
    "error": {"summary": "", "detail": ""},
}


def an_advisory(ident: str = ADVISORY, severity: str = "high") -> dict[str, object]:
    return {
        "source": 1,
        "title": "t",
        "url": f"https://github.com/advisories/{ident}",
        "severity": severity,
    }


def a_report(*vias: object, severity: str = "high") -> dict[str, object]:
    """Un rapport valide : `braces` porte les avis, `micromatch` y mène."""
    vulnerabilities = {
        "braces": {"severity": severity, "via": list(vias)},
        "micromatch": {"severity": severity, "via": ["braces"]},
    }
    counts = {"high": 0, "critical": 0}
    if severity in counts:
        counts[severity] = len(vulnerabilities)
    return {
        "auditReportVersion": 2,
        "vulnerabilities": vulnerabilities,
        "metadata": {"vulnerabilities": counts},
    }


def a_clean_report() -> dict[str, object]:
    return {
        "auditReportVersion": 2,
        "vulnerabilities": {},
        "metadata": {"vulnerabilities": {"high": 0, "critical": 0}},
    }


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
    report = a_report(an_advisory(severity="critical"), severity="critical")
    code, lines = npm_audit.check(report, [an_exception()], TODAY)
    assert code == 1
    assert "critical" in lines[0]


def test_one_unlisted_advisory_among_two_fails() -> None:
    code, _ = npm_audit.check(a_report(an_advisory(), an_advisory(OTHER)), [an_exception()], TODAY)
    assert code == 1


def test_a_moderate_advisory_is_not_blocking() -> None:
    report = a_report(an_advisory(OTHER, "moderate"), severity="moderate")
    assert npm_audit.check(report, [], TODAY) == (0, [])


def test_an_advisory_without_ghsa_is_never_tolerated() -> None:
    advisory = {"source": 7, "title": "t", "url": "https://example.test/x", "severity": "high"}
    code, lines = npm_audit.check(a_report(advisory), [an_exception()], TODAY)
    assert code == 1
    assert "sans-identifiant" in lines[0]


def test_a_stale_exception_is_flagged_without_failing() -> None:
    code, lines = npm_audit.check(a_clean_report(), [an_exception()], TODAY)
    assert code == 0
    assert "à supprimer" in lines[0]


def _files(tmp_path: Path) -> list[str]:
    path = tmp_path / "exceptions.json"
    path.write_text(json.dumps({"exceptions": [an_exception()]}), encoding="utf-8")
    return ["npm_audit.py", str(tmp_path), str(path)]


def _runner(stdout: str, returncode: int = 1) -> Callable[[str], subprocess.CompletedProcess[str]]:
    def run(_: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], returncode, stdout, "boom")

    return run


def _today() -> datetime.date:
    return TODAY


def _main(tmp_path: Path, report: object, returncode: int = 1) -> int:
    out = _runner(report if isinstance(report, str) else json.dumps(report), returncode)
    return int(npm_audit.main(_files(tmp_path), out, _today))


def test_main_passes_on_the_covered_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _main(tmp_path, a_report(an_advisory())) == 0
    assert "toléré" in capsys.readouterr().out


def test_main_fails_without_the_exception(tmp_path: Path) -> None:
    assert _main(tmp_path, a_report(an_advisory(OTHER))) == 1


def test_a_clean_report_passes(tmp_path: Path) -> None:
    assert _main(tmp_path, a_clean_report(), 0) == 0


def test_main_fails_on_invalid_json(tmp_path: Path) -> None:
    assert _main(tmp_path, "pas du json") != 0


def test_main_fails_on_an_npm_error_code(tmp_path: Path) -> None:
    assert _main(tmp_path, a_clean_report(), 2) != 0


def test_main_fails_when_npm_is_missing(tmp_path: Path) -> None:
    def missing(_: str) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("npm")

    assert npm_audit.main(_files(tmp_path), missing, _today) != 0


def test_main_fails_without_the_exceptions_file(tmp_path: Path) -> None:
    argv = ["npm_audit.py", str(tmp_path), str(tmp_path / "absent.json")]
    assert npm_audit.main(argv, _runner("{}"), _today) != 0


def test_main_needs_two_arguments() -> None:
    assert npm_audit.main(["npm_audit.py"], _runner("{}"), _today) == 2


# --- Un audit qui n'a pas eu lieu n'est pas un audit propre --------------------


def test_the_real_registry_error_fails(tmp_path: Path) -> None:
    assert _main(tmp_path, REAL_ERROR) == 2


def test_an_error_code_is_shown_bounded_and_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = {"error": {"code": "ENOTFOUND", "summary": "x\x1b[31m" + "y" * 500}}
    assert _main(tmp_path, report) == 2
    shown = capsys.readouterr().out
    assert "ENOTFOUND" in shown and "\x1b" not in shown and len(shown) < 400


def test_an_empty_object_fails_even_with_exit_zero(tmp_path: Path) -> None:
    assert _main(tmp_path, {}, 0) == 2


def test_no_vulnerabilities_key_but_a_metadata_fails(tmp_path: Path) -> None:
    report = {"auditReportVersion": 2, "metadata": {"vulnerabilities": {"high": 4, "critical": 0}}}
    assert _main(tmp_path, report) == 2


def test_empty_vulnerabilities_against_four_announced_fails(tmp_path: Path) -> None:
    report = a_clean_report()
    report["metadata"] = {"vulnerabilities": {"high": 4, "critical": 0}}
    assert _main(tmp_path, report) == 2


@pytest.mark.parametrize("version", [None, 1, "2"])
def test_the_report_version_must_be_two_or_more(tmp_path: Path, version: object) -> None:
    report = a_clean_report()
    if version is None:
        del report["auditReportVersion"]
    else:
        report["auditReportVersion"] = version
    assert _main(tmp_path, report, 0) == 2


@pytest.mark.parametrize("count", [-1, 1.5, "0", True])
def test_metadata_counts_must_be_whole_numbers(tmp_path: Path, count: object) -> None:
    report = a_clean_report()
    report["metadata"] = {"vulnerabilities": {"high": count, "critical": 0}}
    assert _main(tmp_path, report, 0) == 2


def test_a_high_entry_without_a_readable_advisory_fails(tmp_path: Path) -> None:
    assert _main(tmp_path, a_report()) == 2


def test_an_unknown_severity_fails(tmp_path: Path) -> None:
    assert _main(tmp_path, a_report(an_advisory(), severity="catastrophic")) == 2


# --- `make audit` ne l'avale pas -----------------------------------------------


def _make_audit(tmp_path: Path, npm_json: str, npm_code: int) -> int:
    """Lance la vraie recette `audit` avec de faux `uv` et `npm`, sans réseau."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uv").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bin_dir / "npm").write_text(
        "#!/bin/sh\n"
        f"case \"$*\" in *--json*) cat <<'EOF'\n{npm_json}\nEOF\nexit {npm_code};; esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    for fake in bin_dir.iterdir():
        fake.chmod(0o755)
    frontend = tmp_path / "frontend"
    (frontend / "node_modules").mkdir(parents=True)
    (frontend / "audit-exceptions.json").write_text('{"exceptions": []}', encoding="utf-8")
    stamp = tmp_path / "stamp"
    stamp.write_text("", encoding="utf-8")
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    done = subprocess.run(
        ["make", "-C", str(REPO_ROOT), "audit", f"FRONTEND={frontend}", f"VENV_STAMP={stamp}"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode


@pytest.mark.skipif(shutil.which("make") is None, reason="make absent du poste")
def test_make_audit_fails_when_the_npm_audit_could_not_run(tmp_path: Path) -> None:
    assert _make_audit(tmp_path, json.dumps(REAL_ERROR), 1) != 0


@pytest.mark.skipif(shutil.which("make") is None, reason="make absent du poste")
def test_make_audit_passes_on_a_clean_report(tmp_path: Path) -> None:
    assert _make_audit(tmp_path, json.dumps(a_clean_report()), 0) == 0
