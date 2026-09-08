"""The passage index against a real PostgreSQL with pgvector.

Everything else about the search is proved without a server: the chunker with
strings, the indexer with a fake embedder, the ordering with cosine similarity
in Python. This is the one place that proves the half only PostgreSQL can
answer — that `CREATE EXTENSION vector` applies, that a `vector(1024)` column
accepts what the embedder produces, that `<=>` orders the way the double did,
and that the foreign key really deletes a document's passages with it.

It is the test that would have caught an image without pgvector, an index built
for the wrong operator class, or a cascade that was only ever a comment.

The vectors here are handmade rather than embedded: what is under test is the
storage and the ordering, and a real model would make the expected order a
matter of opinion. See docs/adr/0019.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.documents import Document
from ea.domain.search import EMBEDDING_DIMENSIONS, EmbeddedChunk
from ea.repositories.document_store import PostgresDocumentRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

FIXED_NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
MODEL = "test-embed"


@pytest_asyncio.fixture
async def documents(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> AsyncIterator[PostgresDocumentRepository]:
    await asyncio.to_thread(command.upgrade, alembic_config, "head")
    repository = PostgresDocumentRepository(create_session_factory(postgres_engine))
    try:
        yield repository
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")


def axis(index: int) -> tuple[float, ...]:
    """A unit vector along one axis: cosine distance 0 to itself, 1 to any other.

    Handmade so that the expected ordering is arithmetic rather than an opinion
    about what a language model thinks two sentences have in common.
    """
    return tuple(1.0 if position == index else 0.0 for position in range(EMBEDDING_DIMENSIONS))


def a_document(element_id: UUID | None = None, filename: str = "runbook.md") -> Document:
    return Document.create(
        element_id=element_id or uuid4(),
        filename=filename,
        content="# Runbook\n\nQuoi faire.\n",
        now=FIXED_NOW,
    )


def passages(*specs: tuple[int, tuple[str, ...], str]) -> list[EmbeddedChunk]:
    return [
        EmbeddedChunk(
            ordinal=ordinal,
            heading_path=path,
            text=body,
            embedding=axis(ordinal),
            model=MODEL,
        )
        for ordinal, path, body in specs
    ]


class TestStoringPassages:
    async def test_a_document_and_its_passages_are_written_together(
        self, documents: PostgresDocumentRepository
    ) -> None:
        document = a_document()

        await documents.add(document, passages((0, ("Runbook",), "Quoi faire.")))

        found = await documents.search(axis(0), model=MODEL)
        assert [passage.text for passage in found] == ["Quoi faire."]

    async def test_the_heading_trail_survives_the_round_trip_as_a_path(
        self, documents: PostgresDocumentRepository
    ) -> None:
        """An array and not a joined string: a heading may contain the separator."""
        document = a_document()
        await documents.add(document, passages((0, ("Runbook", "Escalade > vraiment"), "x")))

        found = await documents.search(axis(0), model=MODEL)

        assert found[0].heading_path == ("Runbook", "Escalade > vraiment")
        assert found[0].trail == "runbook.md > Runbook > Escalade > vraiment"

    async def test_accents_survive_the_round_trip(
        self, documents: PostgresDocumentRepository
    ) -> None:
        document = a_document()
        await documents.add(document, passages((0, ("Procédure",), "Redémarrer le nœud.")))

        assert (await documents.search(axis(0), model=MODEL))[0].text == "Redémarrer le nœud."

    async def test_a_revision_replaces_the_passages_rather_than_adding_to_them(
        self, documents: PostgresDocumentRepository
    ) -> None:
        document = a_document()
        await documents.add(document, passages((0, ("Runbook",), "avant")))

        revised = document.revise("# Runbook\n\naprès\n", now=FIXED_NOW)
        await documents.replace(revised, passages((0, ("Runbook",), "après")))

        found = await documents.search(axis(0), model=MODEL, limit=50)
        assert [passage.text for passage in found] == ["après"]

    async def test_a_document_may_be_stored_with_no_passages_at_all(
        self, documents: PostgresDocumentRepository
    ) -> None:
        """`EA_EMBEDDINGS_ENABLED` off: the file is kept, and is not searchable."""
        document = a_document()

        await documents.add(document)

        assert await documents.search(axis(0), model=MODEL) == ()
        assert (await documents.get(document.id)) is not None


class TestOrdering:
    async def test_the_nearest_passage_comes_first(
        self, documents: PostgresDocumentRepository
    ) -> None:
        document = a_document()
        await documents.add(
            document,
            passages(
                (0, ("A",), "loin"),
                (1, ("B",), "proche"),
                (2, ("C",), "loin aussi"),
            ),
        )

        found = await documents.search(axis(1), model=MODEL, limit=3)

        assert found[0].text == "proche"

    async def test_the_score_is_a_similarity_where_larger_is_closer(
        self, documents: PostgresDocumentRepository
    ) -> None:
        document = a_document()
        await documents.add(document, passages((0, ("A",), "identique"), (1, ("B",), "orthogonal")))

        found = await documents.search(axis(0), model=MODEL, limit=2)

        assert found[0].score == pytest.approx(1.0)
        assert found[1].score == pytest.approx(0.0)

    async def test_the_limit_is_honoured(self, documents: PostgresDocumentRepository) -> None:
        document = a_document()
        await documents.add(document, passages(*[(n, ("A",), f"p{n}") for n in range(5)]))

        assert len(await documents.search(axis(0), model=MODEL, limit=2)) == 2


class TestFiltering:
    async def test_a_search_can_be_scoped_to_one_element(
        self, documents: PostgresDocumentRepository
    ) -> None:
        wanted, other = uuid4(), uuid4()
        await documents.add(a_document(wanted, "a.md"), passages((0, ("A",), "chez wanted")))
        await documents.add(a_document(other, "b.md"), passages((0, ("A",), "chez other")))

        found = await documents.search(axis(0), model=MODEL, element_id=wanted)

        assert [passage.text for passage in found] == ["chez wanted"]

    async def test_a_passage_embedded_by_another_model_is_not_a_match(
        self, documents: PostgresDocumentRepository
    ) -> None:
        """A corpus half re-embedded returns too little, visibly — never nonsense."""
        document = a_document()
        await documents.add(document, passages((0, ("A",), "ancienne")))

        assert await documents.search(axis(0), model="un-autre-modele") == ()


class TestTheCascades:
    async def test_deleting_a_document_deletes_its_passages(
        self, documents: PostgresDocumentRepository
    ) -> None:
        """By the foreign key, not by a service — the point of docs/adr/0019."""
        document = a_document()
        await documents.add(document, passages((0, ("A",), "texte")))

        await documents.delete(document.id)

        assert await documents.search(axis(0), model=MODEL) == ()

    async def test_deleting_an_element_deletes_the_passages_of_its_documents(
        self, documents: PostgresDocumentRepository
    ) -> None:
        """Two cascades in a row: the service deletes the rows, the key the rest."""
        element = uuid4()
        await documents.add(a_document(element, "a.md"), passages((0, ("A",), "texte")))

        await documents.discard_for_element(element)

        assert await documents.search(axis(0), model=MODEL) == ()

    async def test_no_passage_can_name_a_document_that_is_not_there(
        self, documents: PostgresDocumentRepository, postgres_engine: AsyncEngine
    ) -> None:
        """The constraint itself, stated: an orphan row is refused by the server."""
        async with postgres_engine.begin() as connection:
            with pytest.raises(Exception, match=r"foreign key|violates"):
                await connection.execute(
                    text(
                        "INSERT INTO document_chunks "
                        "(id, document_id, element_id, ordinal, heading_path, content, "
                        " model, embedding) "
                        "VALUES (:id, :doc, :el, 0, ARRAY['A'], 'x', :model, :vector)"
                    ),
                    {
                        "id": uuid4(),
                        "doc": uuid4(),
                        "el": uuid4(),
                        "model": MODEL,
                        "vector": str(list(axis(0))),
                    },
                )


class TestTheSchemaItself:
    async def test_the_vector_extension_is_installed_and_enabled(
        self, documents: PostgresDocumentRepository, postgres_engine: AsyncEngine
    ) -> None:
        """The prerequisite the image has to satisfy — see docs/adr/0019."""
        async with postgres_engine.connect() as connection:
            installed = await connection.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )

        assert installed.scalar_one_or_none() is not None

    async def test_the_hnsw_index_exists_on_the_embedding_column(
        self, documents: PostgresDocumentRepository, postgres_engine: AsyncEngine
    ) -> None:
        """Without it the search still answers — by scanning every passage."""
        async with postgres_engine.connect() as connection:
            definition = await connection.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE tablename = 'document_chunks' "
                    "AND indexname = 'ix_document_chunks_embedding'"
                )
            )

        created = definition.scalar_one()
        assert "hnsw" in created
        assert "vector_cosine_ops" in created

    async def test_a_vector_of_the_wrong_width_is_refused_by_the_column(
        self, documents: PostgresDocumentRepository
    ) -> None:
        """The check the client makes first, made again by the server."""
        document = a_document()
        narrow = EmbeddedChunk(
            ordinal=0, heading_path=("A",), text="x", embedding=(1.0, 0.0), model=MODEL
        )

        with pytest.raises(Exception, match=r"dimension|expected"):
            await documents.add(document, [narrow])
