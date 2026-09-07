"""How Alembic reaches the database and what it compares against.

Two things are deliberate here. The URL is built from `Settings` rather than
read from `alembic.ini`, so a migration run uses exactly the credentials the
application uses and no committed file carries a password. And the metadata
comes from `ea.db.models`, the one module that imports every mapped table: a
model this package does not import is one autogenerate would propose to drop.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection

from ea.core.config import get_settings
from ea.db.models import Base
from ea.db.postgres import create_engine, dsn_of

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# `compare_type` catches a column whose Python type changed but whose name did
# not — the change autogenerate misses by default and the one that corrupts data
# silently. `compare_server_default` does the same for defaults. Both are spelled
# out at each call site rather than unpacked from a dict, which `context.configure`
# — heavily overloaded — cannot be type-checked through.


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it — `alembic upgrade head --sql`.

    Offline mode never connects; the URL only selects the dialect. It is passed
    with the password masked, so a generated script or a CI log cannot carry
    the secret.
    """
    context.configure(
        url=dsn_of(get_settings()).render_as_string(hide_password=True),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run against a real database, over the same async engine the app uses.

    Alembic's migration API is synchronous, so the work is handed to
    `run_sync`, which drives it on the greenlet SQLAlchemy keeps for exactly
    this. The engine is disposed either way: `alembic` is a short-lived process
    and an undisposed pool keeps it from exiting.
    """
    engine = create_engine(get_settings())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
