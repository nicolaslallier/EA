"""One-off: copy the Neo4j graph into PostgreSQL — docs/adr/0033.

    make graph-import CONFIRM=yes     # from the repository root

Run from `backend/`, with the API stopped (`make app-down`) and a fresh
`make pg-backup`. It writes to the database `backend/.env` names — the shared
one. Steps: `alembic upgrade 0005`, read every element and relationship from
Neo4j, copy them in one transaction, verify the ids on both sides, then
`alembic upgrade head` (revision 0006 and its foreign keys).

The Neo4j driver is not a dependency of the project: `uv run --with neo4j`
loads it for this run only. Deleted, with `ea.graph_import`, once the cut-over
is confirmed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from neo4j import AsyncGraphDatabase
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from ea.core.config import get_settings
from ea.db.postgres import check_connectivity, create_engine, create_session_factory
from ea.graph_import import copy_graph, element_from_neo4j, relationship_from_neo4j, verify_copy

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


async def copy(source: Neo4jSource) -> tuple[int, int]:
    elements_read, relationships_read = await read_neo4j(source)
    elements = [element_from_neo4j(stored) for stored in elements_read]
    relationships = [relationship_from_neo4j(stored) for stored in relationships_read]
    engine = create_engine(get_settings())
    try:
        await check_connectivity(engine)
        sessions = create_session_factory(engine)
        await copy_graph(sessions, elements, relationships)
        await verify_copy(sessions, elements, relationships)
    finally:
        await engine.dispose()
    return len(elements), len(relationships)


def main() -> int:
    source = Neo4jSource()  # type: ignore[call-arg]  # the password comes from the environment
    alembic = Config(BACKEND / "alembic.ini")
    print("alembic upgrade 0005 ...")
    command.upgrade(alembic, "0005")
    print(f"reading {source.uri} ...")
    elements, relationships = asyncio.run(copy(source))
    print(f"{elements} element(s) and {relationships} relationship(s) copied and verified.")
    print("alembic upgrade head ...")
    command.upgrade(alembic, "head")
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
