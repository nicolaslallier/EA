"""The document repository against a real PostgreSQL.

Everything else about documents is proved without a server. This is the one
place that proves the Cypher's counterpart: the DDL of docs/adr/0017 actually
applies, the unique constraint really refuses the second `README.md`, an
accented character survives the round trip as text, and `octet_length` answers
without loading a body.

It is the test that would have caught a `bytea` column, a missing index, or a
timestamp column that dropped its offset.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.documents import Document
from ea.domain.errors import DuplicateDocumentError
from ea.repositories.document_store import PostgresDocumentRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

FIXED_NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def documents(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> AsyncIterator[PostgresDocumentRepository]:
    """The repository over a database at `head`, emptied on the way out.

    The chain is applied rather than `create_all`: what is under test includes
    the migration, and a schema built from the metadata would pass while the
    revision that deploys it was wrong.
    """
    await asyncio.to_thread(command.upgrade, alembic_config, "head")
    repository = PostgresDocumentRepository(create_session_factory(postgres_engine))
    try:
        yield repository
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")


def a_document(element_id: object = None, filename: str = "runbook.md", content: str = "# R\n"):
    return Document.create(
        element_id=element_id or uuid4(),  # type: ignore[arg-type]
        filename=filename,
        content=content,
        now=FIXED_NOW,
    )


async def test_a_document_survives_the_round_trip_as_text(
    documents: PostgresDocumentRepository,
) -> None:
    """Accents and newlines come back exactly as they went in — it is `text`."""
    stored = a_document(content="# Café\n\n- redémarrer\n")

    await documents.add(stored)
    read = await documents.get(stored.id)

    assert read is not None
    assert read.content == stored.content
    assert read.filename == stored.filename
    assert read.created_at == FIXED_NOW


async def test_a_stored_timestamp_keeps_its_offset(
    documents: PostgresDocumentRepository,
) -> None:
    """A naive column would hand back a datetime nobody can place in time."""
    stored = a_document()

    await documents.add(stored)
    read = await documents.get(stored.id)

    assert read is not None
    assert read.updated_at.tzinfo is not None


async def test_the_same_file_name_twice_on_one_element_hits_the_constraint(
    documents: PostgresDocumentRepository,
) -> None:
    """The unique index, not a read-then-insert: two uploads cannot race it."""
    element_id = uuid4()
    await documents.add(a_document(element_id, "README.md"))

    with pytest.raises(DuplicateDocumentError):
        await documents.add(a_document(element_id, "README.md"))


async def test_the_same_file_name_on_two_elements_is_stored_twice(
    documents: PostgresDocumentRepository,
) -> None:
    await documents.add(a_document(uuid4(), "README.md"))
    await documents.add(a_document(uuid4(), "README.md"))


async def test_a_refused_insert_leaves_the_repository_usable(
    documents: PostgresDocumentRepository,
) -> None:
    """Regression: a rolled-back session must not poison the pool.

    The duplicate raises inside `session.begin()`, so the transaction is
    aborted; if the rollback did not happen the next statement would fail with
    "current transaction is aborted" rather than doing its job.
    """
    element_id = uuid4()
    await documents.add(a_document(element_id, "README.md"))
    with pytest.raises(DuplicateDocumentError):
        await documents.add(a_document(element_id, "README.md"))

    await documents.add(a_document(element_id, "runbook.md"))

    assert len(await documents.list_for_element(element_id)) == 2


async def test_a_listing_reports_the_size_without_loading_the_body(
    documents: PostgresDocumentRepository,
) -> None:
    """`octet_length` is computed by the server; the bytes never travel."""
    element_id = uuid4()
    stored = a_document(element_id, content="# Café\n")
    await documents.add(stored)

    [listed] = await documents.list_for_element(element_id)

    assert listed.byte_size == len("# Café\n".encode())
    assert listed.filename == stored.filename


async def test_a_listing_is_ordered_oldest_first(
    documents: PostgresDocumentRepository,
) -> None:
    element_id = uuid4()
    first = a_document(element_id, "a.md")
    second = Document.create(
        element_id=element_id,
        filename="b.md",
        content="# B\n",
        now=FIXED_NOW.replace(year=2027),
    )
    await documents.add(second)
    await documents.add(first)

    listed = await documents.list_for_element(element_id)

    assert [summary.filename for summary in listed] == ["a.md", "b.md"]


async def test_a_listing_of_an_element_with_nothing_attached_is_empty(
    documents: PostgresDocumentRepository,
) -> None:
    assert await documents.list_for_element(uuid4()) == ()


async def test_a_revision_replaces_the_content_and_keeps_the_row(
    documents: PostgresDocumentRepository,
) -> None:
    stored = await documents.add(a_document())
    later = FIXED_NOW.replace(year=2027)

    await documents.replace(stored.revise("# Runbook v2\n", now=later))
    read = await documents.get(stored.id)

    assert read is not None
    assert read.content == "# Runbook v2\n"
    assert read.created_at == FIXED_NOW
    assert read.updated_at == later


async def test_deleting_a_document_reports_whether_there_was_one(
    documents: PostgresDocumentRepository,
) -> None:
    stored = await documents.add(a_document())

    assert await documents.delete(stored.id) is True
    assert await documents.delete(stored.id) is False
    assert await documents.get(stored.id) is None


async def test_discarding_an_element_removes_its_documents_and_no_others(
    documents: PostgresDocumentRepository,
) -> None:
    """The cascade PostgreSQL cannot declare — the element is a node in Neo4j."""
    doomed = uuid4()
    spared = uuid4()
    await documents.add(a_document(doomed, "a.md"))
    await documents.add(a_document(doomed, "b.md"))
    await documents.add(a_document(spared, "a.md"))

    discarded = await documents.discard_for_element(doomed)

    assert discarded == 2
    assert await documents.list_for_element(doomed) == ()
    assert len(await documents.list_for_element(spared)) == 1


async def test_discarding_an_element_that_had_nothing_attached_is_not_an_error(
    documents: PostgresDocumentRepository,
) -> None:
    """Every element deletion calls this, and most elements carry no file."""
    assert await documents.discard_for_element(uuid4()) == 0
