"""Use cases over diagram templates: save a schema as a skeleton, build a diagram from it.

The rule this layer owns: **an instantiation never goes around the architecture service**.
Elements are created by `create_element` (so the ArchiMate and IP-addressing rules apply,
and the review guard on the elements it links) and relationships by `connect`, and only when
the caller asked for them with `create_relationships` - a template's links are what the
author *drew*, not facts nobody decided.

Everything that can be refused is refused before anything is written, and what is written is
written in **one transaction** (`ArchitectureService.transaction`): a failure half-way - a
name already taken by an element, a relationship the guard refuses - leaves no diagram, no
element and no audit event behind.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from ea.domain.archimate import RelationshipType
from ea.domain.audit import AuditAction, template_view
from ea.domain.diagram_objects import DiagramObject, DiagramScene, ObjectKind
from ea.domain.diagram_templates import (
    MAX_SLOTS,
    Binding,
    DiagramTemplate,
    TemplateLink,
    clean_template_name,
    parse_body,
    plan_instantiation,
    template_from_scene,
)
from ea.domain.diagrams import DiagramNode
from ea.domain.errors import TemplateBusyError, TemplateNotFoundError
from ea.services.audit import AuditTrail
from ea.services.caller import logged, require_caller, require_editor
from ea.services.exchange_admission import Admission, Lease

if TYPE_CHECKING:
    from ea.domain.audit import AuditEvent
    from ea.domain.diagrams import DiagramDetail
    from ea.domain.ports import TemplateRepository
    from ea.services.architecture import ArchitectureService
    from ea.services.diagrams import DiagramService

Clock = Callable[[], datetime]

_LINK_PAGE = 200

logger = logging.getLogger(__name__)


#: How many diagrams may be built from templates at once, and how many one caller may hold.
#: One build writes up to `MAX_SLOTS` elements and `MAX_TEMPLATE_LINKS` links in one transaction,
#: so the amplification of a request is bounded here, and the number of requests in flight there.
MAX_BUILDS_IN_FLIGHT = 4
MAX_BUILDS_PER_CALLER = 2
BUILD_LEASE_SECONDS = 60.0


def build_admission(
    *, limit: int = MAX_BUILDS_IN_FLIGHT, clock: Callable[[], float] = time.monotonic
) -> Admission:
    return Admission(
        limit,
        per_caller=min(MAX_BUILDS_PER_CALLER, limit),
        lease_seconds=BUILD_LEASE_SECONDS,
        refusal=lambda: TemplateBusyError("other diagrams are being built: try again shortly"),
        kind="template_build",
        clock=clock,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


class TemplateService:
    def __init__(
        self,
        repository: TemplateRepository,
        architecture: ArchitectureService,
        diagrams: DiagramService,
        *,
        clock: Clock = _utc_now,
        admission: Admission | None = None,
    ) -> None:
        self._admission = admission
        self._repository = repository
        self._architecture = architecture
        self._diagrams = diagrams
        self._now = clock
        self._trail = AuditTrail(clock)

    async def list_templates(self) -> tuple[DiagramTemplate, ...]:
        require_caller()
        return await self._repository.list_all()

    async def get_template(self, template_id: UUID) -> DiagramTemplate:
        require_caller()
        template = await self._repository.get(template_id)
        if template is None:
            msg = f"no template with id {template_id}"
            raise TemplateNotFoundError(msg)
        return template

    def _created_event(self, template: DiagramTemplate) -> tuple[AuditEvent, ...]:
        return (
            self._trail.event(
                AuditAction.TEMPLATE_CREATED, entity_id=template.id, after=template_view(template)
            ),
        )

    async def create_template(
        self, *, name: str, description: str = "", body: Mapping[str, Any]
    ) -> DiagramTemplate:
        require_editor()
        now = self._now()
        template = DiagramTemplate(
            id=uuid4(),
            name=clean_template_name(name),
            description=description.strip(),
            body=parse_body(body),
            created_at=now,
            updated_at=now,
        )

        def created(_: DiagramTemplate) -> tuple[AuditEvent, ...]:
            return self._created_event(template)

        stored = await self._repository.add(template, audit=created)
        self._audit("template created: %r", stored, "template_created")
        return stored

    async def update_template(
        self,
        template_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> DiagramTemplate:
        require_editor()
        current = await self.get_template(template_id)
        revised = replace(
            current,
            name=current.name if name is None else clean_template_name(name),
            description=current.description if description is None else description.strip(),
            body=current.body if body is None else parse_body(body),
            updated_at=self._now(),
        )

        def updated(before: DiagramTemplate) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.TEMPLATE_UPDATED,
                    entity_id=template_id,
                    before=template_view(before),
                    after=template_view(revised),
                ),
            )

        stored = await self._repository.save(revised, audit=updated)
        self._audit("template updated: %r", stored, "template_updated")
        return stored

    async def delete_template(self, template_id: UUID) -> None:
        require_editor()

        def deleted(before: DiagramTemplate) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.TEMPLATE_DELETED,
                    entity_id=template_id,
                    before=template_view(before),
                ),
            )

        if not await self._repository.delete(template_id, audit=deleted):
            msg = f"no template with id {template_id}"
            raise TemplateNotFoundError(msg)
        logger.info(
            "template deleted",
            extra={"action": "template_deleted", "template_id": str(template_id)},
        )

    async def create_from_diagram(
        self, diagram_id: UUID, *, name: str, description: str = ""
    ) -> DiagramTemplate:
        """Keep the shape of a diagram: each element becomes a slot of its type."""
        require_editor()
        detail = await self._diagrams.open(diagram_id)
        elements = {element.id: element for element in detail.graph.elements}
        scene_nodes = tuple(n for n in detail.nodes if n.element_id in elements)
        body = template_from_scene(
            DiagramScene(nodes=scene_nodes, objects=detail.objects, edges=detail.edges),
            elements,
            detail.graph.relationships,
            {
                element.id: element.stereotypes[0].name
                for element in detail.graph.elements
                if element.stereotypes
            },
        )
        now = self._now()
        template = DiagramTemplate(
            id=uuid4(),
            name=clean_template_name(name),
            description=description.strip(),
            body=body,
            created_at=now,
            updated_at=now,
        )

        def created(_: DiagramTemplate) -> tuple[AuditEvent, ...]:
            return self._created_event(template)

        stored = await self._repository.add(template, audit=created)
        self._audit("template saved from a diagram: %r", stored, "template_created")
        return stored

    async def instantiate(
        self,
        template_id: UUID,
        *,
        name: str,
        bindings: Sequence[Binding],
        create_relationships: bool = False,
    ) -> DiagramDetail:
        """Build a diagram from a template, all or nothing.

        First everything that can be refused is: the bindings against the elements'
        real types and stereotypes. Then, in one transaction, the diagram (its name is
        the likeliest collision, so it comes before any element), the elements, the
        relationships only if asked, and the scene.
        """
        caller = require_editor()
        async with self._place(caller.subject):
            return await self._build(
                template_id,
                name=name,
                bindings=bindings,
                create_relationships=create_relationships,
            )

    def _place(self, key: str) -> AbstractAsyncContextManager[Lease | None]:
        return self._admission.lease(key) if self._admission is not None else nullcontext(None)

    async def _build(
        self,
        template_id: UUID,
        *,
        name: str,
        bindings: Sequence[Binding],
        create_relationships: bool,
    ) -> DiagramDetail:
        if len(bindings) > MAX_SLOTS:
            msg = f"a template has at most {MAX_SLOTS} slots to bind"
            raise ValueError(msg)
        template = await self.get_template(template_id)
        body = template.body
        types = {}
        stereotypes: dict[UUID, frozenset[str]] = {}
        for binding in bindings:
            if binding.element_id is not None:
                element = await self._architecture.get_element(binding.element_id)
                types[element.id] = element.element_type
                stereotypes[element.id] = frozenset(ref.name for ref in element.stereotypes)
        plan = plan_instantiation(body, bindings, types, stereotypes)

        async with self._architecture.transaction():
            diagram = await self._diagrams.create(name=name)
            ids: dict[str, UUID] = {slot.key: element_id for slot, element_id in plan.bound}
            for slot, element_name in plan.to_create:
                created = await self._architecture.create_element(
                    element_type=slot.element_type, name=element_name
                )
                ids[slot.key] = created.id
            if create_relationships:
                await self._connect(body.links, ids)
            group_ids = {group.key: uuid4() for group in body.groups}
            nodes = [
                DiagramNode(
                    ids[slot.key],
                    slot.x,
                    slot.y,
                    slot.width,
                    slot.height,
                    parent_object_id=group_ids.get(slot.parent_key) if slot.parent_key else None,
                )
                for slot in body.slots
                if slot.key in ids
            ]
            objects = [
                DiagramObject(
                    group_ids[g.key],
                    ObjectKind.GROUP,
                    g.x,
                    g.y,
                    g.width,
                    g.height,
                    text=g.text,
                    colour=g.colour,
                    z=-1,
                    parent_id=group_ids.get(g.parent_key) if g.parent_key else None,
                )
                for g in body.groups
            ] + [
                DiagramObject(
                    uuid4(),
                    ObjectKind.NOTE,
                    n.x,
                    n.y,
                    n.width,
                    n.height,
                    text=n.text,
                    colour=n.colour,
                    z=1,
                    parent_id=group_ids.get(n.parent_key) if n.parent_key else None,
                    anchor_element_id=ids.get(n.anchor_key) if n.anchor_key else None,
                )
                for n in body.notes
            ]
            await self._diagrams.replace_layout(
                diagram.id, revision=diagram.revision, nodes=nodes, objects=objects
            )
        logger.info(
            "diagram built from a template",
            extra={
                "action": "template_instantiated",
                "template_id": str(template_id),
                "diagram_id": str(diagram.id),
                "slots": len(ids),
                "relationships": create_relationships,
            },
        )
        return await self._diagrams.open(diagram.id)

    async def _already_linked(
        self, source: UUID, target: UUID, relationship_type: RelationshipType
    ) -> bool:
        """Whether this exact link exists - read page by page, so a hub is not cut at a page."""
        offset = 0
        while True:
            page = await self._architecture.list_relationships(
                element_id=source,
                relationship_types=[relationship_type],
                limit=_LINK_PAGE,
                offset=offset,
            )
            if any(r.source_id == source and r.target_id == target for r in page):
                return True
            if len(page) < _LINK_PAGE:
                return False
            offset += _LINK_PAGE

    async def _connect(self, links: Sequence[TemplateLink], ids: Mapping[str, UUID]) -> None:
        """Create the template's links between filled slots - never twice the same one."""
        for link in links:
            source, target = ids.get(link.source_key), ids.get(link.target_key)
            if source is None or target is None:
                continue
            if await self._already_linked(source, target, link.relationship_type):
                continue
            await self._architecture.connect(
                relationship_type=link.relationship_type, source_id=source, target_id=target
            )

    @staticmethod
    def _audit(message: str, template: DiagramTemplate, action: str) -> None:
        logger.info(
            message,
            logged(template.name),
            extra={"action": action, "template_id": str(template.id)},
        )
