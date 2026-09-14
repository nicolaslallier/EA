"""The Alembic chain, checked without a database.

The graph is in PostgreSQL too since docs/adr/0033: every change to a table is a
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


def test_a_document_follows_its_element_by_the_foreign_key() -> None:
    """The element is a row since docs/adr/0033, so the cascade is DDL."""
    element_id = Base.metadata.tables["element_documents"].c.element_id
    key = next(iter(element_id.foreign_keys))

    assert key.column.table.name == "elements"
    assert key.ondelete == "CASCADE"
    assert element_id.index is True


def test_the_passages_table_can_state_the_foreign_key_the_documents_could_not() -> None:
    """The difference docs/adr/0019 turns on: a document *is* a row here.

    Both cascades are DDL now (docs/adr/0033); this one was the first.
    """
    document_id = Base.metadata.tables["document_chunks"].c.document_id
    key = next(iter(document_id.foreign_keys))

    assert key.column.table.name == "element_documents"
    assert key.ondelete == "CASCADE"


def test_the_vector_column_is_as_wide_as_the_one_number_everything_reads() -> None:
    """Settings, migration and model agree by importing, not by retyping."""
    from ea.domain.search import EMBEDDING_DIMENSIONS

    embedding = Base.metadata.tables["document_chunks"].c.embedding

    assert embedding.type.dim == EMBEDDING_DIMENSIONS
    assert embedding.nullable is False


def test_a_passage_records_the_model_that_embedded_it() -> None:
    """Cosine distance between vectors from two models is a meaningless number.

    Storing the model is what lets a search filter on it, which is what makes a
    half-finished reindex return too little rather than something plausible.
    """
    model = Base.metadata.tables["document_chunks"].c.model

    assert model.nullable is False


def test_the_vector_index_is_built_for_the_distance_the_repository_orders_by() -> None:
    """An index built for another operator class is simply never used.

    Nothing fails: the search quietly becomes a full scan of every passage in
    the corpus, and says nothing about it.
    """
    index = next(
        index
        for index in Base.metadata.tables["document_chunks"].indexes
        if index.name == "ix_document_chunks_embedding"
    )

    assert index.dialect_options["postgresql"]["using"] == "hnsw"
    assert index.dialect_options["postgresql"]["ops"] == {"embedding": "vector_cosine_ops"}


def test_a_diagram_node_follows_its_element_by_the_foreign_key() -> None:
    element_id = Base.metadata.tables["diagram_nodes"].c.element_id
    key = next(iter(element_id.foreign_keys))

    assert key.column.table.name == "elements"
    assert key.ondelete == "CASCADE"
    assert element_id.index is True


def test_a_diagram_node_follows_its_diagram_by_the_foreign_key() -> None:
    table = Base.metadata.tables["diagram_nodes"]
    key = next(iter(table.c.diagram_id.foreign_keys))

    assert key.column.table.name == "diagrams"
    assert key.ondelete == "CASCADE"
    assert table.primary_key.name == "pk_diagram_nodes"
    assert [column.name for column in table.primary_key.columns] == ["diagram_id", "element_id"]


def test_a_diagram_name_is_unique_by_a_named_constraint() -> None:
    table = Base.metadata.tables["diagrams"]

    assert "uq_diagrams_name" in {constraint.name for constraint in table.constraints}


def test_an_element_name_is_unique_within_its_type_by_a_named_constraint() -> None:
    """The constraint `pipelines/` detects a duplicate by — docs/adr/0033."""
    table = Base.metadata.tables["elements"]

    assert "uq_elements_element_type_name" in {c.name for c in table.constraints}


def test_an_address_and_a_prefix_are_unique_per_vrf_by_partial_indexes() -> None:
    """The `WHERE` makes each index ignore an element missing either key."""
    indexes = {index.name: index for index in Base.metadata.tables["elements"].indexes}

    for name in ("uq_elements_vrf_ip_address", "uq_elements_vrf_cidr"):
        assert indexes[name].unique is True
        assert indexes[name].dialect_options["postgresql"]["where"] is not None


def test_a_relationship_follows_both_its_ends_by_the_foreign_key() -> None:
    table = Base.metadata.tables["relationships"]

    for column in ("source_id", "target_id"):
        key = next(iter(table.c[column].foreign_keys))
        assert key.column.table.name == "elements"
        assert key.ondelete == "CASCADE"
        assert table.c[column].index is True


def test_user_defined_properties_are_one_jsonb_map() -> None:
    from sqlalchemy.dialects.postgresql import JSONB

    assert isinstance(Base.metadata.tables["elements"].c.properties.type, JSONB)
    assert isinstance(Base.metadata.tables["relationships"].c.properties.type, JSONB)
