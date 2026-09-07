"""The Alembic chain, checked without a database.

The graph has no migrations — its constraints are declared and reapplied at
boot (`db/schema.py`). PostgreSQL is the opposite: every change to a table is a
versioned script, and the properties that make that chain safe are structural,
so they are checked here rather than discovered on a deployment.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from ea.db.base import Base

BACKEND = Path(__file__).parents[2]
ALEMBIC_INI = BACKEND / "alembic.ini"


def _config() -> Config:
    return Config(ALEMBIC_INI)


def test_the_ini_holds_no_connection_string() -> None:
    """No literal DSN in a committed file — `env.py` reads it from `Settings`."""
    assert "sqlalchemy.url" not in ALEMBIC_INI.read_text()


def test_the_script_location_resolves_from_any_working_directory() -> None:
    """`make` runs alembic from `backend/`, pytest from wherever it was called."""
    assert "%(here)s" in ALEMBIC_INI.read_text()

    script = ScriptDirectory.from_config(_config())

    assert Path(script.dir) == BACKEND / "migrations"


def test_the_history_has_exactly_one_head() -> None:
    """Two heads mean two branches, and `upgrade head` stops being defined."""
    assert len(ScriptDirectory.from_config(_config()).get_heads()) == 1


def test_every_revision_but_the_first_names_its_parent() -> None:
    """A chain with a hole cannot be walked back down."""
    revisions = list(ScriptDirectory.from_config(_config()).walk_revisions())

    assert [r for r in revisions if r.down_revision is None] == [revisions[-1]]


def test_constraints_are_named_by_a_convention() -> None:
    """Autogenerate emits whatever name the backend invented otherwise.

    An unnamed constraint is one Alembic cannot drop on the way down: the
    generated `downgrade` has no handle to reference. Fixing the names here
    makes every future migration reversible by construction.
    """
    convention = Base.metadata.naming_convention

    assert set(convention) == {"ix", "uq", "ck", "fk", "pk"}
    assert convention["pk"] == "pk_%(table_name)s"


def test_no_table_is_mapped_yet() -> None:
    """The scaffold deliberately invents no domain: the first table is a choice.

    This fails the day one is added, which is the reminder to generate its
    migration — `make pg-revision m="..."` — rather than to delete the line.
    """
    assert Base.metadata.tables == {}
