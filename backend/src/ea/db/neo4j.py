"""The Neo4j driver: how it is built, checked and shut down.

One driver per process. It owns a connection pool, is safe to share across
requests, and must be closed on shutdown or the event loop is left with open
sockets.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from neo4j import AsyncGraphDatabase
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from ea.db.schema import apply_schema

if TYPE_CHECKING:
    from neo4j import AsyncDriver

    from ea.core.config import Settings

logger = logging.getLogger(__name__)


def create_driver(settings: Settings) -> AsyncDriver:
    """Build the driver. No connection is opened until it is first used."""
    return AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
        max_connection_pool_size=settings.neo4j_max_connection_pool_size,
        connection_acquisition_timeout=settings.neo4j_connection_timeout_seconds,
    )


class GraphUnavailableError(RuntimeError):
    """The graph database could not be reached or refused the credentials."""


async def check_connectivity(driver: AsyncDriver) -> None:
    """Fail loudly at boot rather than on the first request of the first user."""
    try:
        await driver.verify_connectivity()
    except (ServiceUnavailable, Neo4jError) as error:
        # The driver's message can name the host and the auth scheme, so it goes
        # to the log and not to whatever surfaces this exception.
        logger.error("neo4j connectivity check failed", exc_info=error)
        msg = "the architecture graph is unreachable"
        raise GraphUnavailableError(msg) from error


async def prepare_database(driver: AsyncDriver, *, database: str) -> None:
    """Check the connection and bring the schema up to date, in that order."""
    await check_connectivity(driver)
    await apply_schema(driver, database=database)
