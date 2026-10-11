"""Use cases over profiles and the stereotypes applied to elements.

A stereotype is a label on an element of one base type; it adds no type, so the
relationship rules stay those of the base type (docs/adr/0005). The rule this
layer owns: **what an element carries must agree with what it is** - a
stereotype of another base type is refused, two stereotypes declaring one key
cannot be carried together, and the values already there are checked against
the formats declared. `required` is reported by `domain.profiles.missing_required`
and never enforced here. No regular expression is run for a user (docs/adr/0064).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from ea.domain.audit import AuditAction, profile_view, stereotypes_view
from ea.domain.errors import (
    ProfileNotFoundError,
    StereotypeInUseError,
    StereotypeNotFoundError,
)
from ea.domain.profiles import (
    IconKey,
    Profile,
    Stereotype,
    StereotypeProperty,
    profile_from_document,
    profile_to_document,
    validate_stereotype_properties,
)
from ea.services.audit import AuditTrail
from ea.services.caller import logged, require_caller, require_editor

if TYPE_CHECKING:
    from ea.domain.archimate import ElementType
    from ea.domain.audit import AuditEvent
    from ea.domain.model import Element, StereotypeRef
    from ea.domain.ports import ProfileRepository
    from ea.services.architecture import ArchitectureService

Clock = Callable[[], datetime]

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ProfileService:
    """The single entry point `api/` and `mcp/` use for profiles and stereotypes."""

    def __init__(
        self,
        repository: ProfileRepository,
        architecture: ArchitectureService,
        *,
        clock: Clock = _utc_now,
    ) -> None:
        self._repository = repository
        self._architecture = architecture
        self._now = clock
        self._trail = AuditTrail(clock)

    # --- Reading ------------------------------------------------------------

    async def list_profiles(self) -> tuple[Profile, ...]:
        require_caller()
        return await self._repository.list_profiles()

    async def get_profile(self, profile_id: UUID) -> Profile:
        require_caller()
        profile = await self._repository.get_profile(profile_id)
        if profile is None:
            msg = f"no profile with id {profile_id}"
            raise ProfileNotFoundError(msg)
        return profile

    async def list_stereotypes(self) -> tuple[Stereotype, ...]:
        require_caller()
        return await self._repository.list_stereotypes()

    async def export_profile(self, profile_id: UUID) -> dict[str, Any]:
        require_caller()
        return profile_to_document(await self.get_profile(profile_id))

    # --- Profiles -------------------------------------------------------------

    def _created_event(self, profile: Profile) -> tuple[AuditEvent, ...]:
        return (
            self._trail.event(
                AuditAction.PROFILE_CREATED, entity_id=profile.id, after=profile_view(profile)
            ),
        )

    async def create_profile(self, *, name: str, description: str = "") -> Profile:
        require_editor()
        now = self._now()
        profile = Profile(
            id=uuid4(), name=name, description=description.strip(), created_at=now, updated_at=now
        )

        def created(_: Profile) -> tuple[AuditEvent, ...]:
            return self._created_event(profile)

        stored = await self._repository.add_profile(profile, audit=created)
        self._log("profile created", stored.id, "created")
        return stored

    async def update_profile(
        self, profile_id: UUID, *, name: str | None = None, description: str | None = None
    ) -> Profile:
        require_editor()
        current = await self.get_profile(profile_id)
        revised = replace(
            current,
            name=current.name if name is None else name,
            description=current.description if description is None else description.strip(),
            updated_at=self._now(),
        )

        def updated(before: Profile) -> tuple[AuditEvent, ...]:
            return self._updated_event(before, replace(revised, stereotypes=before.stereotypes))

        stored = await self._repository.save_profile(revised, audit=updated)
        self._log("profile updated", stored.id, "updated")
        return stored

    def _updated_event(self, before: Profile, after: Profile) -> tuple[AuditEvent, ...]:
        """The event of a change to a profile: the profile as the write read it, then as it ends."""
        return (
            self._trail.event(
                AuditAction.PROFILE_UPDATED,
                entity_id=before.id,
                before=profile_view(before),
                after=profile_view(after),
            ),
        )

    async def delete_profile(self, profile_id: UUID) -> None:
        require_editor()
        profile = await self.get_profile(profile_id)
        counts = await self._repository.applied_counts([s.id for s in profile.stereotypes])
        if counts:
            msg = (
                f"the profile {profile.name!r} has stereotypes applied to "
                f"{sum(counts.values())} element(s)"
            )
            raise StereotypeInUseError(msg)

        def deleted(before: Profile) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.PROFILE_DELETED, entity_id=profile_id, before=profile_view(before)
                ),
            )

        if not await self._repository.delete_profile(profile_id, audit=deleted):
            msg = f"no profile with id {profile_id}"
            raise ProfileNotFoundError(msg)
        self._log("profile deleted", profile_id, "deleted")

    async def import_profile(self, document: Mapping[str, Any]) -> Profile:
        """A profile from a document, through the validators a manual write uses."""
        require_editor()
        profile = profile_from_document(document, now=self._now())

        def created(_: Profile) -> tuple[AuditEvent, ...]:
            return self._created_event(profile)

        stored = await self._repository.add_profile(profile, audit=created)
        self._log("profile imported", stored.id, "imported")
        return stored

    # --- Stereotypes ------------------------------------------------------------

    async def add_stereotype(
        self,
        profile_id: UUID,
        *,
        name: str,
        base_type: ElementType,
        colour: str | None,
        icon_key: IconKey,
        properties: Sequence[StereotypeProperty],
    ) -> Stereotype:
        require_editor()
        await self.get_profile(profile_id)
        stereotype = Stereotype(
            id=uuid4(),
            profile_id=profile_id,
            name=name,
            base_type=base_type,
            colour=colour,
            icon_key=icon_key,
            properties=tuple(properties),
        )

        def added(before: Profile) -> tuple[AuditEvent, ...]:
            return self._updated_event(
                before, replace(before, stereotypes=(*before.stereotypes, stereotype))
            )

        stored = await self._repository.add_stereotype(stereotype, audit=added)
        self._log("stereotype added", profile_id, "stereotype_added")
        return stored

    async def replace_stereotype(
        self,
        stereotype_id: UUID,
        *,
        name: str,
        base_type: ElementType,
        colour: str | None,
        icon_key: IconKey,
        properties: Sequence[StereotypeProperty],
    ) -> Stereotype:
        require_editor()
        current = await self._stereotype(stereotype_id)
        applied = (await self._repository.applied_counts([stereotype_id])).get(stereotype_id, 0)
        if applied and base_type is not current.base_type:
            msg = (
                f"the stereotype {current.name!r} is applied to {applied} element(s): "
                "its base type cannot change"
            )
            raise StereotypeInUseError(msg)
        replacement = Stereotype(
            id=stereotype_id,
            profile_id=current.profile_id,
            name=name,
            base_type=base_type,
            colour=colour,
            icon_key=icon_key,
            properties=tuple(properties),
        )

        def replaced(before: Profile) -> tuple[AuditEvent, ...]:
            return self._updated_event(
                before,
                replace(
                    before,
                    stereotypes=tuple(
                        replacement if s.id == stereotype_id else s for s in before.stereotypes
                    ),
                ),
            )

        stored = await self._repository.replace_stereotype(replacement, audit=replaced)
        self._log("stereotype replaced", current.profile_id, "stereotype_replaced")
        return stored

    async def delete_stereotype(self, stereotype_id: UUID) -> None:
        require_editor()
        current = await self._stereotype(stereotype_id)
        applied = (await self._repository.applied_counts([stereotype_id])).get(stereotype_id, 0)
        if applied:
            msg = f"the stereotype {current.name!r} is applied to {applied} element(s)"
            raise StereotypeInUseError(msg)

        def removed_from(before: Profile) -> tuple[AuditEvent, ...]:
            return self._updated_event(
                before,
                replace(
                    before,
                    stereotypes=tuple(s for s in before.stereotypes if s.id != stereotype_id),
                ),
            )

        removed = await self._repository.delete_stereotype(stereotype_id, audit=removed_from)
        if not removed:
            raise StereotypeNotFoundError(f"no stereotype with id {stereotype_id}")
        self._log("stereotype deleted", current.profile_id, "stereotype_deleted")

    # --- On elements ------------------------------------------------------------

    async def apply_stereotypes(self, element_id: UUID, stereotype_ids: Sequence[UUID]) -> Element:
        """Make the set of stereotypes an element carries exactly `stereotype_ids`."""
        require_editor()
        element = await self._architecture.get_element(element_id)
        chosen = [await self._stereotype(sid) for sid in dict.fromkeys(stereotype_ids)]
        for stereotype in chosen:
            if stereotype.base_type is not element.element_type:
                msg = (
                    f"stereotype {stereotype.name!r} applies to {stereotype.base_type.value}, "
                    f"not to {element.element_type.value}"
                )
                raise ValueError(msg)
        validate_stereotype_properties(element.properties, chosen)
        after = tuple(sorted((s.ref for s in chosen), key=lambda ref: ref.name))

        def changed(before: tuple[StereotypeRef, ...]) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.ELEMENT_STEREOTYPES_CHANGED,
                    entity_id=element_id,
                    before=stereotypes_view(before),
                    after=stereotypes_view(after),
                ),
            )

        await self._repository.set_for_element(element_id, [s.id for s in chosen], audit=changed)
        # Counts and ids only: a property value may be sensitive.
        logger.info(
            "stereotypes of an element set: %r",
            logged(element.name),
            extra={
                "action": "element_stereotypes_changed",
                "element_id": str(element_id),
                "stereotypes": len(chosen),
            },
        )
        return await self._architecture.get_element(element_id)

    async def apply_stereotype(self, element_id: UUID, stereotype_id: UUID) -> Element:
        require_editor()
        element = await self._architecture.get_element(element_id)
        carried = [ref.id for ref in element.stereotypes]
        return await self.apply_stereotypes(element_id, [*carried, stereotype_id])

    async def remove_stereotype(self, element_id: UUID, stereotype_id: UUID) -> Element:
        """Take the label off. The properties stay: they are the element's own."""
        require_editor()
        element = await self._architecture.get_element(element_id)
        kept = [ref.id for ref in element.stereotypes if ref.id != stereotype_id]
        return await self.apply_stereotypes(element_id, kept)

    async def _stereotype(self, stereotype_id: UUID) -> Stereotype:
        found = await self._repository.get_stereotype(stereotype_id)
        if found is None:
            msg = f"no stereotype with id {stereotype_id}"
            raise StereotypeNotFoundError(msg)
        return found

    @staticmethod
    def _log(message: str, profile_id: UUID, change: str) -> None:
        logger.info(
            message,
            extra={"action": "profile_changed", "profile_id": str(profile_id), "change": change},
        )
