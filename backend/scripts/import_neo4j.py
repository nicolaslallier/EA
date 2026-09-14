"""One-off: copy the Neo4j graph into PostgreSQL — docs/adr/0033.

    make graph-import CONFIRM=yes     # from the repository root

Run from `backend/`, with the API stopped (`make app-down`) and a fresh
`make pg-backup`. It writes to the database `backend/.env` names — the shared
one. Steps, in this order so that nothing is written until the whole graph has
been read: read and convert every element and relationship from Neo4j;
`alembic upgrade 0005`; copy them in one transaction that checks the ids on
both sides before it commits; `alembic upgrade head` (revision 0006 and its
foreign keys). Should anything fail after `0005`, the recovery command is
printed: `cd backend && uv run alembic downgrade 0004`, which the image on
`main` can boot on again.

The Neo4j driver is not a dependency of the project: `uv run --with
'neo4j==6.3.0'` loads it for this run only. Deleted, with `ea.graph_import`,
once the cut-over is confirmed.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from neo4j import AsyncGraphDatabase
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from ea.core.config import Settings, get_settings
from ea.db.postgres import check_connectivity, create_engine, create_session_factory
from ea.domain.model import Element, Relationship
from ea.graph_import import copy_graph, element_from_neo4j, relationship_from_neo4j

BACKEND = Path(__file__).resolve().parents[1]


class Neo4jSource(BaseSettings):
    """Where the old graph is. Read here only — `Settings` no longer knows Neo4j."""

    model_config = SettingsConfigDict(env_prefix="EA_NEO4J_", env_file=".env", extra="ignore")

    uri: str = "bolt://127.0.0.1:7687"
    user: str = "neo4j"
    password: SecretStr
    database: str = "neo4j"


async def read_neo4j(source: Neo4jSource) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    driver = AsyncGraphDatabase.driver(
        source.uri, auth=(source.user, source.password.get_secret_value())
    )
    try:
        nodes = await driver.execute_query(
            "MATCH (e:Element) RETURN properties(e) AS e", database_=source.database
        )
        edges = await driver.execute_query(
            "MATCH (:Element)-[r]->(:Element) RETURN properties(r) AS r",
            database_=source.database,
        )
    finally:
        await driver.close()
    return [dict(record["e"]) for record in nodes.records], [
        dict(record["r"]) for record in edges.records
    ]


async def read_graph(source: Neo4jSource) -> tuple[list[Element], list[Relationship]]:
    """Everything, already converted: a conversion error must surface before any write."""
    elements_read, relationships_read = await read_neo4j(source)
    return (
        [element_from_neo4j(stored) for stored in elements_read],
        [relationship_from_neo4j(stored) for stored in relationships_read],
    )


async def copy(
    settings: Settings, elements: list[Element], relationships: list[Relationship]
) -> None:
    engine = create_engine(settings)
    try:
        await check_connectivity(engine)
        await copy_graph(create_session_factory(engine), elements, relationships)
    finally:
        await engine.dispose()


def main() -> int:
    source = Neo4jSource()  # type: ignore[call-arg]  # the password comes from the environment
    settings = get_settings()
    target = f"{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_database}"
    print(f"will write to PostgreSQL {target}")
    print(f"reading {source.uri} ...")
    elements, relationships = asyncio.run(read_graph(source))
    print(f"{len(elements)} element(s) and {len(relationships)} relationship(s) read.")
    alembic = Config(BACKEND / "alembic.ini")
    # 0005 runs in one transaction: should it fail, the database is still at 0004.
    print(f"alembic upgrade 0005 on {target} ...")
    command.upgrade(alembic, "0005")
    try:
        asyncio.run(copy(settings, elements, relationships))
        print("copied and verified.")
        print("alembic upgrade head ...")
        command.upgrade(alembic, "head")
    except BaseException:
        print(
            f"FAILED after alembic upgrade 0005: {target} is at a revision the image on main "
            "cannot boot on. Put it back with: cd backend && uv run alembic downgrade 0004",
            file=sys.stderr,
        )
        raise
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
