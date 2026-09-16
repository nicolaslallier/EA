"""Ce que les cibles `make` visent, et d'où elles le tiennent.

Une bannière rouge qui annonce « la base PARTAGÉE » avant `ea.reindex` ou
`alembic upgrade head` n'a de valeur que si elle nomme la base à laquelle ces
commandes vont réellement se connecter. Les coordonnées étaient des constantes
du Makefile (`127.0.0.1`, `ea`) tandis que le Python lisait `EA_POSTGRES_*`
dans `backend/.env` : sur un poste qui n'héberge pas la base, la bannière
désignait la boucle locale et la migration partait ailleurs.

Les tests lancent `make -n`, qui développe les recettes sans rien exécuter :
aucune connexion, aucun mot de passe lu, et `BE_ENV` pointé sur un fichier
temporaire pour ne jamais dépendre du `backend/.env` du poste.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make absent du poste")

# Les mêmes noms que DOTENV_GET_PY lit dans l'environnement : un poste qui les
# exporte pour son propre usage ne doit pas décider du résultat d'un test.
_OVERRIDING = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_DB",
    "EA_POSTGRES_HOST",
    "EA_POSTGRES_PORT",
    "EA_POSTGRES_USER",
    "EA_POSTGRES_DATABASE",
    "EMBEDDINGS_URL",
    "EA_EMBEDDINGS_BASE_URL",
    "EA_EMBEDDINGS_MODEL",
    "POSTGRES_PASSWORD",
    "EA_POSTGRES_PASSWORD",
)


def _run(target: str, env_file: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    environment = {name: value for name, value in os.environ.items() if name not in _OVERRIDING}
    return subprocess.run(
        ["make", "--no-print-directory", *flags, target, f"BE_ENV={env_file}"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _dry_run(target: str, env_file: Path) -> str:
    """Ce que `make` exécuterait pour cette cible, sans l'exécuter."""
    completed = _run(target, env_file, "-n")
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


@pytest.fixture
def an_env_file(tmp_path: Path) -> Path:
    """Un `backend/.env` désignant une base et un embedder qui ne sont pas les défauts."""
    path = tmp_path / ".env"
    path.write_text(
        "EA_POSTGRES_HOST=192.168.2.10\n"
        "EA_POSTGRES_PORT=5442\n"
        "EA_POSTGRES_USER=eabis\n"
        "EA_POSTGRES_DATABASE=eadb\n"
        "EA_EMBEDDINGS_BASE_URL=http://192.168.2.10:11435/v1\n"
        "EA_EMBEDDINGS_MODEL=un-autre-modele\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def an_empty_env_file(tmp_path: Path) -> Path:
    path = tmp_path / "vide.env"
    path.write_text("EA_DEBUG=true\n", encoding="utf-8")
    return path


class TestTheBannerNamesTheDatabaseTheCommandWillOpen:
    def test_docs_reindex(self, an_env_file: Path) -> None:
        assert "192.168.2.10/eadb" in _dry_run("docs-reindex", an_env_file)

    def test_pg_migrate(self, an_env_file: Path) -> None:
        assert "192.168.2.10/eadb" in _dry_run("pg-migrate", an_env_file)


class TestThePsqlTargetsFollowTheSameConfiguration:
    def test_pg_ping_connects_where_the_application_does(self, an_env_file: Path) -> None:
        recipe = _dry_run("pg-ping", an_env_file)
        assert "-h 192.168.2.10 -p 5442 -U eabis -d eadb" in recipe

    def test_pg_backup_names_the_database_it_dumps(self, an_env_file: Path) -> None:
        assert "postgres-eadb-" in _dry_run("pg-backup", an_env_file)


class TestTheEmbedderToo:
    def test_embed_ping_asks_the_service_the_application_uses(self, an_env_file: Path) -> None:
        recipe = _dry_run("embed-ping", an_env_file)
        assert "http://192.168.2.10:11435/v1/embeddings" in recipe
        assert "un-autre-modele" in recipe


class TestThePasswordStillComesFromTheSameFile:
    """`require-postgres-password` est la seule cible qui lit un secret.

    Elle est exécutée pour de vrai — elle n'ouvre aucune connexion, elle
    vérifie seulement que le shell de la recette a trouvé quelque chose.
    """

    def test_it_passes_when_the_env_file_carries_one(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("EA_POSTGRES_PASSWORD=pas-un-vrai\n", encoding="utf-8")
        assert _run("require-postgres-password", env_file).returncode == 0

    def test_it_refuses_when_nothing_carries_one(self, an_empty_env_file: Path) -> None:
        assert _run("require-postgres-password", an_empty_env_file).returncode != 0


class TestTheDefaultsStandWhenNothingIsConfigured:
    def test_the_shared_database_of_adr_0029(self, an_empty_env_file: Path) -> None:
        assert "127.0.0.1/ea" in _dry_run("docs-reindex", an_empty_env_file)

    def test_the_embedder_of_adr_0038(self, an_empty_env_file: Path) -> None:
        assert "http://192.168.2.10:11435/v1/embeddings" in _dry_run(
            "embed-ping", an_empty_env_file
        )
