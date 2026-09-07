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


def test_every_mapped_table_is_reachable_from_the_models_package() -> None:
    """Autogenerate proposes to *drop* a table whose module nobody imported.

    Importing `ea.db.models` is what `migrations/env.py` does and all it does,
    so the metadata this sees is exactly the metadata a generated migration is
    diffed against.
    """
    from ea.db import models

    mapped = {mapper.class_.__tablename__ for mapper in models.Base.registry.mappers}

    assert mapped == set(Base.metadata.tables)


def test_the_documents_table_states_its_constraints_by_convention() -> None:
    """The names a `downgrade` has to be able to reference — see docs/adr/0015."""
    table = Base.metadata.tables["element_documents"]

    assert table.primary_key.name == "pk_element_documents"
    assert {constraint.name for constraint in table.constraints} >= {
        "pk_element_documents",
        "uq_element_documents_element_id_filename",
    }


def test_the_markdown_is_stored_as_text_and_not_as_bytes() -> None:
    """The whole point of docs/adr/0017: markdown is prose, read by people.

    A `bytea` column would make searching, diffing and reading it a decoding
    step, and would let in a file that is not text at all.
    """
    from sqlalchemy import Text

    content = Base.metadata.tables["element_documents"].c.content

    assert isinstance(content.type, Text)
    assert content.nullable is False


def test_the_element_a_document_names_carries_no_foreign_key() -> None:
    """It cannot: the element is a `:Element` node in Neo4j, not a row here.

    This is the assertion that explains the explicit cascade in
    `ArchitectureService.delete_element` — see docs/adr/0017.
    """
    element_id = Base.metadata.tables["element_documents"].c.element_id

    assert element_id.foreign_keys == set()
    assert element_id.index is True
