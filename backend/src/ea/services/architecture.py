"""Use cases over the architecture graph.

This layer owns the rules that need more than one object to check: an element
must exist before it can be linked, and a containment edge must not close a
loop. The rules that need only the two *types* live in the domain and are
enforced by `Relationship.between`.

When authentication lands, the permission checks go here — never in the router
and never in the SPA, per `CLAUDE.md`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from ea.domain.approval import (
    Transition,
    WriteKind,
    apply_transition,
    guard_element_write,
    needs_approver,
    reviewed_after_change,
)
from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.domain.audit import AuditAction, cascaded_view, element_view, relationship_view
from ea.domain.errors import CyclicContainmentError, ElementNotFoundError
from ea.domain.ipam import validate_ipam_properties
from ea.domain.model import Element, Relationship, reject_nul
from ea.domain.ports import ElementChange, EndsReview, GraphView, Transaction
from ea.domain.profiles import validate_stereotype_properties
from ea.domain.timeline import validate_lifespan_properties
from ea.services.audit import AuditTrail
from ea.services.caller import logged, require_approver, require_caller, require_editor
from ea.services.refusals import logged_refusal

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

    from ea.domain.audit import AuditEvent
    from ea.domain.ports import (
        ArchitectureRepository,
        ElementFilter,
        ElementRemoval,
        StereotypeSource,
    )

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]

#: Relationships that build the containment tree, and so must stay acyclic.
_CONTAINMENT = frozenset({RelationshipType.COMPOSITION, RelationshipType.AGGREGATION})

#: The journal action of each review transition.
_ACTION_OF = {
    Transition.SUBMIT: AuditAction.ELEMENT_SUBMITTED,
    Transition.APPROVE: AuditAction.ELEMENT_APPROVED,
    Transition.REJECT: AuditAction.ELEMENT_REJECTED,
    Transition.REOPEN: AuditAction.ELEMENT_REOPENED,
    Transition.LOCK: AuditAction.ELEMENT_LOCKED,
    Transition.UNLOCK: AuditAction.ELEMENT_UNLOCKED,
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ArchitectureService:
    """The single entry point `api/` uses to read and change the graph."""

    def __init__(
        self,
        repository: ArchitectureRepository,
        *,
        clock: Clock = _utc_now,
        separate_duties: bool = True,
        stereotypes: StereotypeSource | None = None,
    ) -> None:
        self._repository = repository
        self._stereotypes = stereotypes
        self._now = clock
        self._trail = AuditTrail(clock)
        self._separate_duties = separate_duties
        if not separate_duties:
            logger.warning(
                "separation of duties is OFF (EA_APPROVAL_SEPARATION=false): an approver "
                "may approve what they changed last",
                extra={"action": "separation_disabled"},
            )

    async def _decorated(self, elements: Sequence[Element]) -> tuple[Element, ...]:
        """The elements with the stereotypes they carry - a label, shown wherever they are read."""
        if self._stereotypes is None or not elements:
            return tuple(elements)
        refs = await self._stereotypes.refs_of([element.id for element in elements])
        return tuple(replace(element, stereotypes=refs.get(element.id, ())) for element in elements)

    def _review_of_ends(self) -> EndsReview:
        """What a link change does to its two ends, applied by the repository under their locks.

        A locked end refuses the change outright; an approved one is allowed and
        goes back to `in_review` — saved in the same transaction as the link,
        never approved in silence.
        """

        def change(end: Element) -> Element:
            guard_element_write(end, WriteKind.LINK)
            return reviewed_after_change(end)

        def audit(changed: ElementChange) -> tuple[AuditEvent, ...]:
            if changed.before.approval_state is changed.after.approval_state:
                return ()  # only the revision moved: the link's own event says it all
            return (
                self._trail.event(
                    AuditAction.ELEMENT_UPDATED,
                    entity_id=changed.before.id,
                    before=element_view(changed.before),
                    after=element_view(changed.after),
                ),
            )

        return EndsReview(change=change, audit=audit)

    def transaction(self, *, commit: bool = True) -> AbstractAsyncContextManager[Transaction]:
        """One transaction for several use cases that each call this service (docs/adr/0052).

        Every use case inside still asks who is calling; nothing is written by entering it.
        """
        return self._repository.transaction(commit=commit)

    # --- Elements ---------------------------------------------------------

    async def create_element(
        self,
        *,
        element_type: ElementType,
        name: str,
        description: str = "",
        documentation: str = "",
        properties: Mapping[str, str] | None = None,
    ) -> Element:
        require_editor()
        properties = validate_lifespan_properties(
            validate_ipam_properties(element_type, properties)
        )
        element = Element.create(
            element_type=element_type,
            name=name,
            description=description,
            documentation=documentation,
            properties=properties,
            now=self._now(),
        )

        def created(stored: Element) -> tuple[AuditEvent, ...]:
            # Built by the repository inside the write, so the moment is the write's.
            return (
                self._trail.event(
                    AuditAction.ELEMENT_CREATED, entity_id=stored.id, after=element_view(stored)
                ),
            )

        stored = await self._repository.add_element(element, audit=created)
        logger.info(
            "element created: %s %r",
            stored.element_type.value,
            logged(stored.name),
            extra={
                "action": "created",
                "element_id": str(stored.id),
                "element_type": stored.element_type.value,
            },
        )
        return stored

    async def get_element(self, element_id: UUID) -> Element:
        """Fetch an element or say which one is missing."""
        require_caller()
        element = await self._repository.get_element(element_id)
        if element is None:
            msg = f"no element with id {element_id}"
            raise ElementNotFoundError(msg)
        return (await self._decorated([element]))[0]

    async def list_elements(self, criteria: ElementFilter) -> tuple[Element, ...]:
        require_caller()
        return await self._decorated(await self._repository.list_elements(criteria))

    async def count_elements(self, criteria: ElementFilter) -> int:
        require_caller()
        return await self._repository.count_elements(criteria)

    async def update_element(
        self,
        element_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        documentation: str | None = None,
        properties: Mapping[str, str] | None = None,
    ) -> Element:
        """Apply a partial update. An omitted field keeps its stored value.

        The element type is deliberately not updatable: changing it could make
        relationships that are already stored illegal, which is a migration, not
        an edit.
        """
        caller = require_editor()
        # Only to refuse a missing element and to know its (immutable) type.
        current = await self.get_element(element_id)
        properties = validate_lifespan_properties(
            validate_ipam_properties(current.element_type, properties)
        )
        if properties is not None and self._stereotypes is not None:
            # The same rule behind every door (docs/adr/0020): a value that breaks the format
            # a carried stereotype declares is refused here as it is by `apply_stereotypes`.
            # `required` is never checked.
            validate_stereotype_properties(
                properties, await self._stereotypes.stereotypes_of(element_id)
            )

        def patch(stored: Element) -> Element:
            """Pure; the repository applies it to the row it holds under lock.

            Review and locking are decided here, on that row and not on the
            copy read above: a lock put on the element meanwhile still refuses.
            """
            guard_element_write(stored, WriteKind.CONTENT, caller)
            now = self._now()
            updated = stored
            if name is not None:
                updated = updated.rename(name, now=now)
            if properties is not None:
                updated = updated.with_properties(properties, now=now)
            if description is not None or documentation is not None:
                updated = replace(
                    updated,
                    updated_at=now,
                    description=(
                        reject_nul(description, "a description").strip()
                        if description is not None
                        else updated.description
                    ),
                    documentation=(
                        reject_nul(documentation, "a documentation").strip()
                        if documentation is not None
                        else updated.documentation
                    ),
                )
            if replace(updated, updated_at=stored.updated_at) != stored:
                updated = reviewed_after_change(updated)
            return updated

        return await self._write_patch(element_id, patch)

    async def complete_element(
        self,
        element_id: UUID,
        *,
        description: str = "",
        properties: Mapping[str, str] | None = None,
    ) -> Element:
        """Fill what is blank, and nothing else: a description, properties that are absent or empty.

        Whether a field is blank is decided on the row as the repository holds it under
        lock, never on a copy read earlier — a value somebody wrote in between is kept.
        With nothing left to fill, nothing is written: no revision, no event, no return to
        review. It never reopens a review: an approved or locked element is refused for every
        role, an approver included (the guard is asked with no caller, which lifts nothing).
        """
        require_editor()
        # Only to refuse a missing element and to know its (immutable) type.
        current = await self.get_element(element_id)
        filling = dict(properties or {})
        validate_ipam_properties(current.element_type, {**current.properties, **filling})

        def patch(stored: Element) -> Element:
            """Pure; applied to the row under lock. Returns `stored` itself if all is filled."""
            fill = {k: v for k, v in filling.items() if not stored.properties.get(k)}
            if fill:
                # Judged on the row under lock, and stored in its canonical spelling.
                merged = validate_lifespan_properties({**stored.properties, **fill}) or {}
                fill = {k: merged[k] for k in fill if k in merged}
            wants_description = bool(description.strip()) and not stored.description
            if not fill and not wants_description:
                return stored
            guard_element_write(stored, WriteKind.CONTENT)
            now = self._now()
            updated = stored
            if fill:
                updated = updated.with_properties({**stored.properties, **fill}, now=now)
            if wants_description:
                text = reject_nul(description, "a description").strip()
                updated = replace(updated, updated_at=now, description=text)
            return updated

        return await self._write_patch(element_id, patch)

    async def _write_patch(self, element_id: UUID, patch: Callable[[Element], Element]) -> Element:
        def events(change: ElementChange) -> tuple[AuditEvent, ...]:
            # Called by the repository in the transaction, on the row it patched.
            return (
                self._trail.event(
                    AuditAction.ELEMENT_UPDATED,
                    entity_id=change.before.id,
                    before=element_view(change.before),
                    after=element_view(change.after),
                ),
            )

        with logged_refusal("lock_refused", [element_id]):
            stored = await self._repository.update_element(element_id, patch, audit=events)
        logger.info(
            "element updated: %s %r",
            stored.element_type.value,
            logged(stored.name),
            extra={
                "action": "updated",
                "element_id": str(stored.id),
                "element_type": stored.element_type.value,
            },
        )
        return (await self._decorated([stored]))[0]

    async def delete_element(self, element_id: UUID) -> None:
        """Remove an element together with everything attached to it.

        Its relationships, its documents with their passages and its boxes on
        diagrams follow by foreign keys, in the one transaction that deletes
        it — see docs/adr/0033.
        """
        caller = require_editor()

        def events(removal: ElementRemoval) -> tuple[AuditEvent, ...]:
            # What went with the element is read by the repository in the same
            # transaction as the DELETE, so the list is what the cascade removed.
            return (
                self._trail.event(
                    AuditAction.ELEMENT_DELETED,
                    entity_id=element_id,
                    before={
                        **element_view(removal.element),
                        "cascaded": cascaded_view(
                            relationship_count=removal.relationship_count,
                            relationships=[
                                (link.id, link.relationship_type.value)
                                for link in removal.relationships
                            ],
                            document_count=removal.documents.count,
                            document_ids=removal.documents.ids,
                            diagram_count=removal.diagrams.count,
                            diagram_ids=removal.diagrams.ids,
                        ),
                    },
                ),
            )

        def guard(stored: Element) -> None:
            guard_element_write(stored, WriteKind.CONTENT, caller)

        with logged_refusal("lock_refused", [element_id]):
            deleted = await self._repository.delete_element(
                element_id, audit=events, guard=guard, ends=self._review_of_ends()
            )
        if not deleted:
            msg = f"no element with id {element_id}"
            raise ElementNotFoundError(msg)
        logger.info(
            "element deleted, with everything attached to it",
            extra={"action": "deleted", "element_id": str(element_id)},
        )

    async def transition_element(
        self,
        element_id: UUID,
        transition: Transition,
        *,
        expected_revision: int | None = None,
    ) -> Element:
        """Move an element through review: submit, approve, reject, reopen, lock, unlock.

        The caller is the one the adapter proved (the token's roles), never a
        field of the request. Everything is decided by the repository on the row
        it holds locked, in the transaction that also writes the one event:
        the role, the state, the revision (`approve` needs the `expected_revision`
        the approver read — the others certify no content and ignore it) and the
        separation of duties.
        """
        caller = require_caller()
        if needs_approver(transition):
            require_approver()
        else:
            require_editor()

        def change(stored: Element) -> Element:
            return apply_transition(
                stored,
                transition,
                caller,
                now=self._now(),
                last_author=stored.last_modified_by_sub,
                expected_revision=expected_revision,
                separate_duties=self._separate_duties,
            )

        def events(changed: ElementChange) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    _ACTION_OF[transition],
                    entity_id=element_id,
                    before=element_view(changed.before),
                    after=element_view(changed.after),
                ),
            )

        with logged_refusal("transition_refused", [element_id]):
            stored = await self._repository.transition_element(element_id, change, audit=events)
        logger.info(
            "element %r: %s",
            logged(stored.name),
            transition.value,
            extra={
                "action": f"element_{transition.value}",
                "element_id": str(element_id),
                "transition": transition.value,
            },
        )
        return (await self._decorated([stored]))[0]

    # --- Relationships ----------------------------------------------------

    async def connect(
        self,
        *,
        relationship_type: RelationshipType,
        source_id: UUID,
        target_id: UUID,
        name: str = "",
        access_type: AccessType | None = None,
        directed: bool = False,
        properties: Mapping[str, str] | None = None,
    ) -> Relationship:
        """Link two elements, refusing anything the metamodel or the graph forbids."""
        require_editor()
        properties = validate_lifespan_properties(properties)
        source = await self.get_element(source_id)
        target = await self.get_element(target_id)

        # `between` rejects the pairs ArchiMate does not allow; it cannot see the
        # rest of the graph, so the containment loop is checked separately.
        relationship = Relationship.between(
            relationship_type,
            source,
            target,
            name=name,
            access_type=access_type,
            directed=directed,
            properties=properties,
            now=self._now(),
        )
        if (
            relationship_type in _CONTAINMENT
            and await self._repository.would_close_a_containment_cycle(source_id, target_id)
        ):
            msg = (
                f"{source.name!r} already sits inside {target.name!r}: "
                "containment would become cyclic"
            )
            raise CyclicContainmentError(msg)

        def created(stored: Relationship) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.RELATIONSHIP_CREATED,
                    entity_id=stored.id,
                    after=relationship_view(stored),
                ),
            )

        with logged_refusal("lock_refused", [source_id, target_id]):
            stored = await self._repository.add_relationship(
                relationship, audit=created, ends=self._review_of_ends()
            )
        logger.info(
            "%r --%s--> %r",
            logged(source.name),
            stored.relationship_type.value,
            logged(target.name),
            extra={
                "action": "connected",
                "relationship_id": str(stored.id),
                "relationship_type": stored.relationship_type.value,
                "source_id": str(source_id),
                "target_id": str(target_id),
            },
        )
        return stored

    async def get_relationship(self, relationship_id: UUID) -> Relationship:
        require_caller()
        relationship = await self._repository.get_relationship(relationship_id)
        if relationship is None:
            msg = f"no relationship with id {relationship_id}"
            raise ElementNotFoundError(msg)
        return relationship

    async def list_relationships(
        self,
        *,
        element_id: UUID | None = None,
        relationship_types: Sequence[RelationshipType] = (),
        limit: int = 50,
        offset: int = 0,
        as_of: date | None = None,
    ) -> tuple[Relationship, ...]:
        require_caller()
        return await self._repository.list_relationships(
            element_id=element_id,
            relationship_types=relationship_types,
            limit=limit,
            offset=offset,
            as_of=as_of,
        )

    async def disconnect(self, relationship_id: UUID) -> None:
        require_editor()

        def events(before: Relationship) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.RELATIONSHIP_DELETED,
                    entity_id=relationship_id,
                    before=relationship_view(before),
                ),
            )

        with logged_refusal("lock_refused"):
            deleted = await self._repository.delete_relationship(
                relationship_id, audit=events, ends=self._review_of_ends()
            )
        if not deleted:
            msg = f"no relationship with id {relationship_id}"
            raise ElementNotFoundError(msg)
        logger.info(
            "relationship deleted",
            extra={"action": "disconnected", "relationship_id": str(relationship_id)},
        )

    async def relations_of(
        self,
        element_id: UUID,
        *,
        relationship_types: Sequence[RelationshipType] = (),
    ) -> GraphView:
        """Everything an element is linked to, ready to be listed or drawn."""
        require_caller()
        await self.get_element(element_id)
        return await self._repository.relations_of(
            element_id, relationship_types=relationship_types
        )

    async def view_of(self, element_ids: Sequence[UUID]) -> GraphView:
        """The elements among these ids that still exist, and the links between them."""
        require_caller()
        graph = await self._repository.view_of(element_ids)
        return GraphView(
            elements=await self._decorated(graph.elements), relationships=graph.relationships
        )

    # --- Analysis ---------------------------------------------------------

    async def neighbourhood(
        self,
        element_id: UUID,
        *,
        depth: int = 1,
        relationship_types: Sequence[RelationshipType] = (),
        as_of: date | None = None,
    ) -> GraphView:
        """The sub-graph around an element — what a diagram of it would show.

        On `as_of`, an element that exists but is not in service that day gives an empty
        sub-graph (docs/adr/0060), not a 404.
        """
        require_caller()
        await self.get_element(element_id)
        return await self._repository.neighbourhood(
            element_id, depth=depth, relationship_types=relationship_types, as_of=as_of
        )

    async def impact_of(
        self,
        element_id: UUID,
        *,
        depth: int = 5,
        relationship_types: Sequence[RelationshipType] = (),
        as_of: date | None = None,
    ) -> GraphView:
        """Everything that would be affected if this element stopped working."""
        require_caller()
        await self.get_element(element_id)
        return await self._repository.impacted_by(
            element_id, depth=depth, relationship_types=relationship_types, as_of=as_of
        )
