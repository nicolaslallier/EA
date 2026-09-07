"""The PostgreSQL engine: how it is addressed, built, checked and shut down.

One engine per process. It owns a connection pool, is safe to share across
requests, and must be disposed on shutdown or the event loop is left with open
sockets — the same contract as the Neo4j driver next door.

Nothing stores anything here yet (docs/adr/0015). What exists is the seam: a
DSN built from settings without string surgery, an engine, a session factory,
and a boot-time check that fails loudly instead of on a user's first request.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if TYPE_CHECKING:
    from ea.core.config import Settings

logger = logging.getLogger(__name__)

#: asyncpg rather than psycopg: the stack is async end to end, and the driver
#: is the one SQLAlchemy documents for `postgresql+asyncpg`.
DRIVER = "postgresql+asyncpg"


def dsn_of(settings: Settings) -> URL:
    """Assemble the connection URL from its parts.

    `URL.create` rather than an f-string: a password containing `@`, `/` or `:`
    — which a generated one usually does — silently addresses a *different*
    database when spliced into a URL by hand. It also renders as
    `postgresql+asyncpg://ea:***@host/ea` in logs and tracebacks, so the engine
    built from it cannot print the secret.
    """
    return URL.create(
        DRIVER,
        username=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_database,
    )


def create_engine(settings: Settings) -> AsyncEngine:
    """Build the engine. No connection is opened until it is first used."""
    return create_async_engine(
        dsn_of(settings),
        pool_size=settings.postgres_pool_size,
        max_overflow=settings.postgres_max_overflow,
        # A pooled connection a restarted server has already forgotten fails on
        # use, not on checkout; the ping turns that into a transparent reconnect.
        pool_pre_ping=True,
        # asyncpg spells its connect timeout `timeout`.
        connect_args={"timeout": settings.postgres_connection_timeout_seconds},
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """The factory a request-scoped session comes from.

    `expire_on_commit=False` because the default expires every loaded attribute
    at commit: the next read of an object a route has already returned would
    emit a query, and in async code that query happens after the session is
    gone.
    """
    return async_sessionmaker(engine, expire_on_commit=False)


class RelationalStoreUnavailableError(RuntimeError):
    """PostgreSQL could not be reached or refused the credentials."""


async def check_connectivity(engine: AsyncEngine) -> None:
    """Fail loudly at boot rather than on the first request of the first user.

    The `except` is deliberately blind. Narrowing it to `SQLAlchemyError` let a
    wrong password escape as asyncpg's own `InvalidPasswordError` — which names
    the role and is not part of SQLAlchemy's hierarchy at connect time. There
    is no failure of this probe that means anything other than "the store is
    not usable", so every one of them is translated, and the original goes to
    the log where the host, the port and the role belong.
    """
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as error:
        logger.error("postgres connectivity check failed", exc_info=error)
        msg = "the relational store is unreachable"
        raise RelationalStoreUnavailableError(msg) from error
