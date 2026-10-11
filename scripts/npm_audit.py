"""Auditer les dépendances de développement du frontend, exceptions nommées comprises.

    python3 scripts/npm_audit.py frontend frontend/audit-exceptions.json

Lit `npm audit --json` dans le dossier donné. Chaque avis `high` ou `critical`
doit figurer, par IDENTIFIANT (GHSA-…), dans une exception non expirée du
fichier de données. Rien d'autre ne couvre une alerte : ni le nom du paquet, ni
le fait qu'il « ait l'air » d'un outil de développement.

- une exception ne couvre que `high`, jamais `critical` ;
- une exception expirée ne couvre plus rien : le garde-fou revient seul ;
- une exception qui ne correspond plus à aucun avis est signalée (à supprimer),
  sans faire échouer ;
- une erreur de `npm` (absent, code autre que 0 ou 1, JSON illisible) fait
  échouer : un audit qui n'a pas eu lieu ne vaut pas un audit propre.

Les dépendances de production n'ont aucune exception : le Makefile les audite
d'abord par `npm audit --omit=dev`. Voir docs/audit-exceptions.md.

Stdlib seulement : le Makefile l'appelle avec le `python3` du poste.
"""

from __future__ import annotations

import datetime
import json
import re
import subprocess  # nosec B404 - lance `npm`, un exécutable fixe, sans shell
import sys
from collections.abc import Callable
from pathlib import Path

_GHSA = re.compile(r"GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}", re.IGNORECASE)
_BLOCKING = ("high", "critical")

Runner = Callable[[str], "subprocess.CompletedProcess[str]"]


def run_npm_audit(directory: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 B607 - arguments fixes, pas de shell
        ["npm", "audit", "--json"],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
    )


_SEVERITIES = ("info", "low", "moderate", "high", "critical")


class Invalid(ValueError):
    """Le rapport n'est pas celui d'un audit mené à son terme."""


def _plain(text: object) -> str:
    """Un texte venu d'ailleurs, sans caractères de contrôle, borné à 200."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "", str(text))[:200]


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate(report: object) -> dict[str, dict[str, object]]:
    """Les vulnérabilités du rapport, ou `Invalid` : ici l'absence ne vaut jamais « rien »."""
    if not isinstance(report, dict):
        raise Invalid("le rapport n'est pas un objet")
    if "error" in report or "message" in report:
        error = report.get("error")
        detail = error if isinstance(error, dict) else {}
        raise Invalid(
            "npm signale une erreur : "
            f"{_plain(detail.get('code', ''))} {_plain(detail.get('summary', ''))} "
            f"{_plain(report.get('message', ''))}".strip()
        )
    version = report.get("auditReportVersion")
    if not _count(version) or version < 2:  # type: ignore[operator]
        raise Invalid("auditReportVersion absent ou inférieur à 2")
    vulnerabilities = report.get("vulnerabilities")
    if not isinstance(vulnerabilities, dict):
        raise Invalid("« vulnerabilities » absent ou n'est pas un objet")
    metadata = report.get("metadata")
    counts = metadata.get("vulnerabilities") if isinstance(metadata, dict) else None
    if not isinstance(counts, dict) or not _count(counts.get("high")) or not _count(
        counts.get("critical")
    ):
        raise Invalid("« metadata.vulnerabilities » absent ou sans high/critical entiers")
    blocking = 0
    for name, vulnerability in vulnerabilities.items():
        if not isinstance(vulnerability, dict) or not isinstance(vulnerability.get("via"), list):
            raise Invalid(f"{_plain(name)} : entrée illisible")
        severities = [vulnerability.get("severity")] + [
            v.get("severity") for v in vulnerability["via"] if isinstance(v, dict)
        ]
        unknown = [s for s in severities if s not in _SEVERITIES]
        if unknown:
            raise Invalid(f"{_plain(name)} : gravité inconnue {_plain(unknown[0])}")
        if vulnerability["severity"] in _BLOCKING:
            blocking += 1
    announced = counts["high"] + counts["critical"]
    if blocking != announced:
        raise Invalid(f"{blocking} entrées high/critical lues, {announced} annoncées")
    for name, vulnerability in vulnerabilities.items():
        if vulnerability["severity"] in _BLOCKING and not _backed(name, vulnerabilities, set()):
            raise Invalid(f"{_plain(name)} : high/critical sans avis lisible")
    return vulnerabilities


def _backed(name: str, vulnerabilities: dict[str, dict[str, object]], seen: set[str]) -> bool:
    """L'entrée porte un avis (objet), ou mène par ses `via` à une entrée qui en porte un."""
    if name in seen or name not in vulnerabilities:
        return False
    seen.add(name)
    via = vulnerabilities[name]["via"]
    assert isinstance(via, list)
    return any(
        isinstance(v, dict) or (isinstance(v, str) and _backed(v, vulnerabilities, seen))
        for v in via
    )


def advisories(report: object) -> list[tuple[str, str, str, str]]:
    """(paquet, identifiant, gravité, titre) de chaque avis high/critical.

    Seuls les `via` qui sont des objets sont des avis ; les chaînes nomment le
    paquet par lequel un autre est atteint, et n'ont pas d'identifiant propre.
    """
    found: list[tuple[str, str, str, str]] = []
    for name, vulnerability in validate(report).items():
        for via in vulnerability["via"]:  # type: ignore[attr-defined]
            if not isinstance(via, dict) or via.get("severity") not in _BLOCKING:
                continue
            match = _GHSA.search(str(via.get("url", "")))
            ident = match.group(0) if match else f"sans-identifiant:{via.get('source')}"
            found.append((name, ident, via["severity"], str(via.get("title", ""))))
    return found


def check(
    report: object,
    exceptions: list[dict[str, str]],
    today: datetime.date,
) -> tuple[int, list[str]]:
    """Le code de sortie et les lignes à afficher."""
    by_id = {e["advisory"].upper(): e for e in exceptions}
    lines: list[str] = []
    failed = False
    seen: set[str] = set()
    for package, ident, severity, title in advisories(report):
        seen.add(ident.upper())
        exception = by_id.get(ident.upper())
        if severity == "critical":
            reason = "gravité critical : aucune exception ne la couvre"
        elif exception is None:
            reason = "non couvert par audit-exceptions.json"
        elif datetime.date.fromisoformat(exception["expires"]) < today:
            reason = f"exception expirée le {exception['expires']}"
        else:
            lines.append(f"toléré  {package}  {ident}  jusqu'au {exception['expires']}")
            continue
        failed = True
        lines.append(f"ÉCHEC   {package}  {ident}  ({severity}) {title} — {reason}")
    for ident, exception in by_id.items():
        if ident not in seen:
            lines.append(f"périmée {ident} ne correspond plus à aucune alerte : à supprimer")
    return (1 if failed else 0), lines


def main(
    argv: list[str],
    runner: Runner = run_npm_audit,
    today: Callable[[], datetime.date] = datetime.date.today,
) -> int:
    if len(argv) != 3:
        sys.stderr.write("usage : npm_audit.py DOSSIER EXCEPTIONS.json\n")
        return 2
    try:
        exceptions = json.loads(Path(argv[2]).read_text(encoding="utf-8"))["exceptions"]
        done = runner(argv[1])
        if done.returncode not in (0, 1):
            raise ValueError(f"npm a rendu {done.returncode} : {done.stderr.strip()}")
        code, lines = check(json.loads(done.stdout), exceptions, today())
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        # json.JSONDecodeError est une ValueError ; FileNotFoundError une OSError.
        sys.stdout.write(f"ÉCHEC   l'audit n'a pas pu avoir lieu : {_plain(error)}\n")
        return 2
    sys.stdout.write("\n".join(lines) + ("\n" if lines else ""))
    return code


if __name__ == "__main__":  # pragma: no cover - le point d'entrée lui-même
    raise SystemExit(main(sys.argv))
