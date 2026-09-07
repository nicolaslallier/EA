"""The relational scaffold against a real PostgreSQL.

Everything else about PostgreSQL is checked without a server. This is the one
place that proves the assembled thing works: the DSN addresses a database that
answers, the migration chain runs on it and comes back down, and an application
with `postgres_enabled` on boots instead of hanging on a pool it cannot fill.

It is the test that would have caught a bad driver name, an `env.py` that
cannot resolve `ea`, or a revision whose `downgrade` was never written.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.api.dependencies import session_factory_of
from ea.core.config import Settings
from ea.db.models import Base
from ea.db.postgres import create_session_factory
from ea.main import create_app
from ea.services.architecture import ArchitectureService

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def test_a_session_reaches_the_server_and_comes_back(postgres_engine: AsyncEngine) -> None:
    """`postgres_engine` already proved connectivity; this proves the factory."""
    async with create_session_factory(postgres_engine)() as session:
        answer = await session.execute(text("SELECT 1"))

    assert answer.scalar_one() == 1


async def test_the_migration_chain_runs_and_reverses(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    """`upgrade head` then `downgrade base`, on whatever state the database is in.

    Alembic's own `env.py` calls `asyncio.run`, so it cannot be invoked from
    inside this running loop: `to_thread` gives it a thread with no loop of its
    own, which is exactly what it expects.
    """
    await asyncio.to_thread(command.upgrade, alembic_config, "head")

    async with postgres_engine.connect() as connection:
        stamped = await connection.execute(text("SELECT version_num FROM alembic_version"))
        version = stamped.scalar_one()

    assert version == ScriptDirectory.from_config(alembic_config).get_current_head()

    await asyncio.to_thread(command.downgrade, alembic_config, "base")

    async with postgres_engine.connect() as connection:
        remaining = await connection.execute(text("SELECT count(*) FROM alembic_version"))

    assert remaining.scalar_one() == 0


async def test_the_chain_builds_every_table_the_models_declare(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    """A revision nobody wrote is a table autogenerate would propose to drop.

    Comparing the applied schema to `Base.metadata` is what catches a model
    added without its migration — the failure that only shows up on the next
    deployment otherwise.
    """
    await asyncio.to_thread(command.upgrade, alembic_config, "head")

    async with postgres_engine.connect() as connection:
        tables = await connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )

    assert {row[0] for row in tables} == {"alembic_version", *Base.metadata.tables}


async def test_the_application_boots_with_the_relational_store_open(
    postgres_engine: AsyncEngine,
    service: ArchitectureService,
) -> None:
    """The lifespan opens the pool, checks it, and hands out a session factory.

    The graph is injected rather than opened: what is under test is the
    relational half of the lifespan, and requiring Neo4j here would make this
    skip on every machine that has PostgreSQL and no access to the cluster.
    """
    app = create_app(Settings(debug=True, postgres_enabled=True), architecture_service=service)
    transport = httpx.ASGITransport(app=app)

    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        app.router.lifespan_context(app),
    ):
        answered = await client.get("/health")
        session_factory_of(app)

    assert answered.status_code == 200
