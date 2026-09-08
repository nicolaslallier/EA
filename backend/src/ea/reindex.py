"""Rebuild the semantic index over every stored document.

    uv run python -m ea.reindex      # or `make docs-reindex` from the repo root

An index that is written on every upload can still fall behind its store, in
exactly two ways, and both of them are configuration rather than bugs: a
document attached while `EA_EMBEDDINGS_ENABLED` was off was stored and never
cut up, and a corpus whose embedding model changed matches nothing at all until
it has been embedded again — see docs/adr/0019, which explains why the second
is deliberately loud rather than quietly approximate.

This is the catch-up for both. It is a script and not an endpoint because it is
an operator's job with no request behind it, it can take a while over a large
corpus, and running it twice is harmless: every document is simply re-cut and
re-embedded from the text the store holds.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from ea.core.config import Settings, get_settings
from ea.db.neo4j import create_driver
from ea.db.postgres import check_connectivity, create_engine, create_session_factory
from ea.main import build_embedder
from ea.repositories.archimate_graph import Neo4jArchitectureRepository
from ea.repositories.document_store import PostgresDocumentRepository
from ea.services.architecture import ArchitectureService
from ea.services.documents import DocumentService
from ea.services.indexing import DocumentIndexer

logger = logging.getLogger(__name__)


async def reindex(settings: Settings) -> int:
    """Re-cut and re-embed every stored document, and say how many.

    Everything is checked before the first document is read — the store answers
    and the model is the width of the column — so a misconfigured run stops at
    once instead of halfway through a corpus.

    The Neo4j driver is opened because `DocumentService` is built with an
    architecture service, which owns the rule that an element must exist before
    a file hangs off it. Reindexing never asks it anything, and the driver
    connects lazily, so this costs nothing beyond the object.
    """
    engine = create_engine(settings)
    embedder = build_embedder(settings)
    driver = create_driver(settings)
    try:
        await check_connectivity(engine)
        await embedder.probe()
        documents = PostgresDocumentRepository(create_session_factory(engine))
        architecture = ArchitectureService(
            Neo4jArchitectureRepository(driver, database=settings.neo4j_database),
            attachments=documents,
        )
        service = DocumentService(documents, architecture, indexer=DocumentIndexer(embedder))
        return await service.reindex_all()
    finally:
        await embedder.aclose()
        await driver.close()
        await engine.dispose()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    settings = get_settings()
    if not settings.embeddings_enabled:
        print("EA_EMBEDDINGS_ENABLED is off — there is no index to rebuild.", file=sys.stderr)
        return 1
    print(f"reindexing with {settings.embeddings_model} at {settings.embeddings_base_url} ...")
    indexed = asyncio.run(reindex(settings))
    print(f"{indexed} document(s) reindexed.")
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point itself
    raise SystemExit(main())
