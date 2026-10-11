"""The saved-query repository against a real PostgreSQL."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import DuplicateSavedQueryError, InvalidQueryError, SavedQueryNotFoundError
from ea.domain.query import (
    Direction,
    ElementQuery,
    PropertyOp,
    PropertyTest,
    QueryOrder,
    RelationTest,
)
from ea.domain.saved_queries import SavedQuery
from ea.repositories.saved_query_store import PostgresSavedQueryRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def a_saved(name: str = "Les noeuds", **query: object) -> SavedQuery:
    return SavedQuery.create(
        name=name,
        query=ElementQuery(**query),  # type: ignore[arg-type]
        created_by="editor",
        now=NOW,
        description="d",
    )


@pytest.fixture
def store(engine_at_head: AsyncEngine) -> PostgresSavedQueryRepository:
    return PostgresSavedQueryRepository(create_session_factory(engine_at_head))


async def corrupt(engine: AsyncEngine, stored: str) -> str:
    """A row whose query the code can no longer read, written behind the repository's back."""
    identifier = str(uuid4())
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO saved_queries (id, name, description, query, created_by, "
                "created_at, updated_at) VALUES (:id, :name, '', CAST(:query AS jsonb), "
                "'someone', now(), now())"
            ),
            {"id": identifier, "name": f"corrompue {identifier[:4]}", "query": stored},
        )
    return identifier


async def test_a_full_query_comes_back_equal(store: PostgresSavedQueryRepository) -> None:
    saved = a_saved(
        element_types=(E.NODE,),
        text="db",
        properties=(PropertyTest("owner", PropertyOp.EQ, "é'\"x"),),
        relations=(RelationTest(R.SERVING, Direction.IN, False, E.BUSINESS_PROCESS),),
        order=QueryOrder.CREATED_AT,
        limit=10,
    )

    await store.add(saved, audit=())

    assert await store.get(saved.id) == saved


async def test_a_taken_name_is_refused_on_add_and_on_save(
    store: PostgresSavedQueryRepository,
) -> None:
    await store.add(a_saved("A"), audit=())
    other = await store.add(a_saved("B"), audit=())

    with pytest.raises(DuplicateSavedQueryError):
        await store.add(a_saved("A"), audit=())
    with pytest.raises(DuplicateSavedQueryError):
        await store.save(other.revise(now=NOW, name="A"), audit=())


async def test_save_replaces_the_query_and_never_the_author(
    store: PostgresSavedQueryRepository,
) -> None:
    saved = await store.add(a_saved(), audit=())

    await store.save(saved.revise(now=NOW, query=ElementQuery(text="neuf")), audit=())

    reread = await store.get(saved.id)
    assert reread is not None
    assert (reread.query.text, reread.created_by) == ("neuf", "editor")


async def test_saving_what_is_gone_is_a_not_found(store: PostgresSavedQueryRepository) -> None:
    with pytest.raises(SavedQueryNotFoundError):
        await store.save(a_saved(), audit=())


async def test_delete_says_whether_it_removed_something(
    store: PostgresSavedQueryRepository,
) -> None:
    saved = await store.add(a_saved(), audit=())

    assert await store.delete(saved.id, audit=()) is True
    assert await store.delete(saved.id, audit=()) is False
    assert await store.get(saved.id) is None


async def test_the_list_is_sorted_and_survives_a_row_it_cannot_read(
    engine_at_head: AsyncEngine, store: PostgresSavedQueryRepository
) -> None:
    await store.add(a_saved("beta"), audit=())
    await store.add(a_saved("Alpha"), audit=())
    broken = await corrupt(engine_at_head, '{"version": 2}')

    listed = await store.list_all()

    assert [s.name for s in listed][:2] == ["Alpha", "beta"]
    assert len(listed) == 3
    with pytest.raises(InvalidQueryError, match="version"):
        await store.get(UUID(broken))


@pytest.mark.parametrize("stored", ['"oops"', "null", "[1]", '{"version": 1}'])
async def test_whatever_was_stored_is_refused_cleanly(
    engine_at_head: AsyncEngine, store: PostgresSavedQueryRepository, stored: str
) -> None:
    broken = await corrupt(engine_at_head, stored)

    with pytest.raises(InvalidQueryError):
        await store.get(UUID(broken))


async def test_two_creations_of_the_same_name_at_once_let_one_through(
    store: PostgresSavedQueryRepository,
) -> None:
    """The constraint, not a read before the insert, decides who wins."""
    results = await asyncio.gather(
        store.add(a_saved("Course"), audit=()),
        store.add(a_saved("Course"), audit=()),
        return_exceptions=True,
    )

    assert sum(isinstance(r, SavedQuery) for r in results) == 1
    assert sum(isinstance(r, DuplicateSavedQueryError) for r in results) == 1
    assert len(await store.list_all()) == 1


async def test_a_populated_base_goes_up_to_0010_and_back_down_to_0009(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    await asyncio.to_thread(command.upgrade, alembic_config, "0009")
    try:
        async with postgres_engine.begin() as connection:
            # Test setup with literal values only: an element that predates saved queries.
            await connection.execute(
                text(
                    "INSERT INTO elements (id, element_type, layer, aspect, name, created_at, "
                    "updated_at) VALUES (gen_random_uuid(), 'node', 'technology', "
                    "'active_structure', 'db-01', now(), now())"
                )
            )

        await asyncio.to_thread(command.upgrade, alembic_config, "0010")
        store = PostgresSavedQueryRepository(create_session_factory(postgres_engine))
        await store.add(a_saved(), audit=())
        await asyncio.to_thread(command.downgrade, alembic_config, "0009")

        async with postgres_engine.connect() as connection:
            # Catalogue reads with no runtime value: nothing to bind.
            kept = await connection.execute(text("SELECT count(*) FROM elements"))
            gone = await connection.execute(text("SELECT to_regclass('saved_queries')"))
        assert kept.scalar_one() == 1
        assert gone.scalar_one() is None
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")
