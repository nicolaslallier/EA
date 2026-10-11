"""An event is in the database if and only if the write it describes is."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.audit import AuditAction, AuditChannel, AuditEvent, AuditFilter, EntityKind
from ea.domain.errors import DuplicateElementError, ElementNotFoundError
from ea.domain.model import Element, Relationship
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.audit_store import PostgresAuditRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def event_for(action: AuditAction, entity_id: object, *, at: datetime = NOW) -> AuditEvent:
    return AuditEvent(
        id=uuid4(),
        at=at,
        actor_sub="editor-1",
        actor_name="editor",
        action=action,
        entity_kind=action.kind,
        entity_id=entity_id,  # type: ignore[arg-type]
        before=None,
        after={"x": 1},
        request_id="req",
        channel=AuditChannel.HTTP,
    )


def an_element(name: str) -> Element:
    return Element.create(element_type=E.NODE, name=name, now=NOW)


async def test_a_refused_write_leaves_no_event_and_a_deletion_leaves_its_trace(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    trail = PostgresAuditRepository(sessions)
    first, clone = an_element("db-01"), an_element("db-01")

    await graph.add_element(first, audit=(event_for(AuditAction.ELEMENT_CREATED, first.id),))
    with pytest.raises(DuplicateElementError):
        await graph.add_element(clone, audit=(event_for(AuditAction.ELEMENT_CREATED, clone.id),))
    ghost = Relationship.between(R.ASSOCIATION, first, an_element("other"), now=NOW)
    with pytest.raises(ElementNotFoundError):
        await graph.add_relationship(
            ghost, audit=(event_for(AuditAction.RELATIONSHIP_CREATED, ghost.id),)
        )
    assert (
        await graph.delete_element(
            uuid4(), audit=(event_for(AuditAction.ELEMENT_DELETED, uuid4()),)
        )
        is False
    )

    only_the_first = await trail.page(AuditFilter())
    assert [e.entity_id for e in only_the_first.events] == [first.id]

    assert await graph.delete_element(
        first.id,
        audit=(event_for(AuditAction.ELEMENT_DELETED, first.id, at=NOW + timedelta(seconds=1)),),
    )
    history = await trail.page(AuditFilter(entity_kind=EntityKind.ELEMENT, entity_id=first.id))
    assert [e.action for e in history.events] == [
        AuditAction.ELEMENT_DELETED,
        AuditAction.ELEMENT_CREATED,
    ]
    assert await graph.get_element(first.id) is None  # the element is gone, its story is not


async def test_a_save_and_a_link_write_their_events_with_them(engine_at_head: AsyncEngine) -> None:
    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    trail = PostgresAuditRepository(sessions)
    left, right = an_element("a"), an_element("b")
    await graph.add_element(left, audit=())
    await graph.add_element(right, audit=())

    renamed = left.rename("a2", now=NOW)
    await graph.save_element(renamed, audit=(event_for(AuditAction.ELEMENT_UPDATED, left.id),))
    link = Relationship.between(R.ASSOCIATION, renamed, right, now=NOW)
    await graph.add_relationship(
        link, audit=(event_for(AuditAction.RELATIONSHIP_CREATED, link.id),)
    )
    await graph.delete_relationship(
        link.id, audit=(event_for(AuditAction.RELATIONSHIP_DELETED, link.id),)
    )

    actions = [e.action for e in (await trail.page(AuditFilter())).events]
    assert sorted(a.value for a in actions) == [
        "element.updated",
        "relationship.created",
        "relationship.deleted",
    ]


async def test_a_removal_names_what_the_foreign_keys_take(
    engine_at_head: AsyncEngine,
) -> None:
    from ea.domain.diagrams import Diagram, DiagramNode
    from ea.domain.documents import Document
    from ea.domain.ports import ElementRemoval
    from ea.repositories.diagram_store import PostgresDiagramRepository
    from ea.repositories.document_store import PostgresDocumentRepository

    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    element = await graph.add_element(an_element("db-01"), audit=())
    document = await PostgresDocumentRepository(sessions).add(
        Document.create(element_id=element.id, filename="a.md", content="# A\n", now=NOW),
        audit=(),
    )
    diagrams = PostgresDiagramRepository(sessions)
    diagram = await diagrams.add(Diagram.create(name="Vue", now=NOW), audit=())
    await diagrams.replace_layout(diagram.id, [DiagramNode(element.id, 0, 0)], now=NOW, audit=())
    seen: list[ElementRemoval] = []

    def record(removal: ElementRemoval) -> tuple[()]:
        seen.append(removal)
        return ()

    assert await graph.delete_element(element.id, audit=record)

    (removal,) = seen
    assert removal.element.id == element.id
    assert removal.document_ids == (document.id,)
    assert removal.diagram_ids == (diagram.id,)


async def test_a_document_and_its_event_commit_or_fail_together(
    engine_at_head: AsyncEngine,
) -> None:
    from ea.domain.documents import Document
    from ea.domain.errors import DuplicateDocumentError
    from ea.repositories.document_store import PostgresDocumentRepository

    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    documents = PostgresDocumentRepository(sessions)
    trail = PostgresAuditRepository(sessions)
    element = await graph.add_element(an_element("db-01"), audit=())
    first = Document.create(element_id=element.id, filename="a.md", content="# A\n", now=NOW)
    second = Document.create(element_id=element.id, filename="a.md", content="# B\n", now=NOW)

    await documents.add(first, audit=(event_for(AuditAction.DOCUMENT_ATTACHED, first.id),))
    with pytest.raises(DuplicateDocumentError):
        await documents.add(second, audit=(event_for(AuditAction.DOCUMENT_ATTACHED, second.id),))
    assert (
        await documents.delete(uuid4(), audit=(event_for(AuditAction.DOCUMENT_DISCARDED, None),))
        is False
    )

    page = await trail.page(AuditFilter(entity_kind=EntityKind.DOCUMENT))
    assert [e.entity_id for e in page.events] == [first.id]


async def test_a_diagram_layout_and_its_event_commit_together(engine_at_head: AsyncEngine) -> None:
    from ea.domain.diagrams import Diagram, DiagramNode
    from ea.repositories.diagram_store import PostgresDiagramRepository

    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    diagrams = PostgresDiagramRepository(sessions)
    trail = PostgresAuditRepository(sessions)
    element = await graph.add_element(an_element("db-01"), audit=())
    diagram = await diagrams.add(
        Diagram.create(name="Vue", now=NOW),
        audit=(event_for(AuditAction.DIAGRAM_CREATED, None),),
    )

    assert not await diagrams.replace_layout(
        uuid4(),
        [DiagramNode(element.id, 0, 0)],
        now=NOW,
        audit=(event_for(AuditAction.DIAGRAM_LAYOUT_REPLACED, None),),
    )
    assert await diagrams.replace_layout(
        diagram.id,
        [DiagramNode(element.id, 0, 0)],
        now=NOW,
        audit=(event_for(AuditAction.DIAGRAM_LAYOUT_REPLACED, diagram.id),),
    )

    actions = [
        e.action for e in (await trail.page(AuditFilter(entity_kind=EntityKind.DIAGRAM))).events
    ]
    assert sorted(a.value for a in actions) == ["diagram.created", "diagram.layout_replaced"]
