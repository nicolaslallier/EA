"""Lire une valeur de `backend/.env` comme l'application la lit.

    python3 backend/scripts/dotenv_get.py backend/.env POSTGRES_HOST EA_POSTGRES_HOST

Le Makefile en a besoin pour deux choses que rien d'autre ne tient ensemble :
le mot de passe qu'il passe à `psql`, `pg_dump` et `pg_restore`, et les
coordonnées qu'il *annonce* avant une commande qui touche la base partagée.
Une bannière rouge qui nomme une base autre que celle que la commande va
ouvrir est pire qu'aucune bannière.

Les règles sont celles de pydantic-settings, pas celles du shell : `export `
toléré, guillemets retirés, commentaire de fin de ligne ignoré hors
guillemets, dernière définition gagnante. Une variable d'environnement non
vide passe avant le fichier, comme pour l'application. Plusieurs noms peuvent
être donnés : le premier renseigné gagne, dans l'environnement d'abord.

Un fichier absent n'est pas une erreur — un poste neuf n'a pas encore de
`.env` —, il ne donne simplement aucune valeur, et le Makefile applique son
défaut. Rien n'est écrit sur la sortie d'erreur : ce script est lu par
`$(shell ...)`, et un message y deviendrait une valeur.

Ce script n'importe rien du projet : le Makefile l'appelle avec le `python3`
du poste, avant que le venv existe.
"""

from __future__ import annotations

import os
import re
import sys

_DEFINITION = re.compile(r"\s*(?:export\s+)?([A-Za-z_]\w*)\s*=\s*(.*)")
_QUOTED = re.compile(r"""(['"])(.*?)\1\s*(?:#.*)?\Z""")


def _unquote(raw: str) -> str:
    """La valeur telle que pydantic-settings la lirait, guillemets compris."""
    quoted = _QUOTED.match(raw)
    if quoted is None:
        # Sans guillemets, un `#` précédé d'un blanc ouvre un commentaire.
        return re.split(r"\s+#", raw, maxsplit=1)[0]
    if quoted.group(1) == "'":
        return quoted.group(2)
    return quoted.group(2).replace('\\"', '"').replace("\\\\", "\\")


def value_of(path: str, names: list[str]) -> str:
    """Le premier de `names` qui soit renseigné : l'environnement, sinon le fichier."""
    for name in names:
        from_environment = os.environ.get(name)
        if from_environment:
            return from_environment
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return ""
    value = ""
    for line in lines:
        definition = _DEFINITION.match(line)
        if definition is not None and definition.group(1) in names:
            value = _unquote(definition.group(2).strip())
    return value


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        return 2
    # Sans saut de ligne : `$(...)` du shell et `$(shell ...)` de make en
    # ajouteraient un, et une valeur n'en porte pas.
    sys.stdout.write(value_of(argv[1], argv[2:]))
    return 0


if __name__ == "__main__":  # pragma: no cover - le point d'entrée lui-même
    raise SystemExit(main(sys.argv))
