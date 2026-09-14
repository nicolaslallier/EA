"""Deleting an element takes everything attached to it, by the foreign keys.

Since revision 0006 (docs/adr/0033) the cascade is DDL: the relationships, the
documents, their passages and the boxes on diagrams all follow the element in
the one transaction that deletes it. Nothing is left for a service to discard.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.models.chunk import DocumentChunk
from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.diagrams import Diagram, DiagramNode
from ea.domain.documents import Document
from ea.domain.model import Element, Relationship
from ea.domain.search import EMBEDDING_DIMENSIONS, EmbeddedChunk
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.diagram_store import PostgresDiagramRepository
from ea.repositories.document_store import PostgresDocumentRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

FIXED_NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


async def test_deleting_an_element_takes_its_links_documents_passages_and_boxes(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    documents = PostgresDocumentRepository(sessions)
    diagrams = PostgresDiagramRepository(sessions)
    doomed = await graph.add_element(
        Element.create(element_type=E.APPLICATION_COMPONENT, name="Billing", now=FIXED_NOW)
    )
    spared = await graph.add_element(
        Element.create(element_type=E.NODE, name="db-01", now=FIXED_NOW)
    )
    await graph.add_relationship(Relationship.between(R.ASSOCIATION, doomed, spared, now=FIXED_NOW))
    document = await documents.add(
        Document.create(element_id=doomed.id, filename="a.md", content="# A\n", now=FIXED_NOW),
        [
            EmbeddedChunk(
                ordinal=0,
                heading_path=("A",),
                text="texte",
                embedding=(1.0,) + (0.0,) * (EMBEDDING_DIMENSIONS - 1),
                model="test-embed",
            )
        ],
    )
    kept = await documents.add(
        Document.create(element_id=spared.id, filename="b.md", content="# B\n", now=FIXED_NOW)
    )
    diagram = await diagrams.add(Diagram.create(name="Vente", description="", now=FIXED_NOW))
    await diagrams.replace_layout(
        diagram.id,
        [DiagramNode(doomed.id, 0, 0), DiagramNode(spared.id, 1, 1)],
        now=FIXED_NOW,
    )

    assert await graph.delete_element(doomed.id) is True

    assert await graph.list_relationships(element_id=spared.id) == ()
    assert await documents.get(document.id) is None
    assert await documents.get(kept.id) is not None
    assert await diagrams.nodes_of(diagram.id) == (DiagramNode(spared.id, 1, 1),)
    async with sessions() as session:
        passages = await session.scalar(select(func.count()).select_from(DocumentChunk))
    assert passages == 0
