"""Profiles through the service: rules behind every door, and what a reader may do."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from ea.domain.approval import Transition
from ea.domain.archimate import ElementType
from ea.domain.audit import AuditAction
from ea.domain.errors import (
    DuplicateProfileError,
    ElementLockedError,
    NotAuthorisedError,
    ProfileLimitError,
    StereotypeInUseError,
    StereotypeNotFoundError,
)
from ea.domain.ports import ElementFilter
from ea.domain.profiles import MAX_PROFILES, IconKey, PropertyKind, StereotypeProperty
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.profiles import ProfileService
from tests.conftest import InMemoryAuditLog, InMemoryProfiles, a_reader, an_approver

TIER = StereotypeProperty(
    key="tier", label="Niveau", kind=PropertyKind.ENUM, allowed_values=("1", "2")
)
OWNER = StereotypeProperty(key="owner", label="Responsable", required=True)


async def a_stereotype(
    profiles: ProfileService,
    *props: StereotypeProperty,
    name: str = "database",
    base: ElementType = ElementType.APPLICATION_COMPONENT,
    profile_name: str = "SI",
):
    profile = await profiles.create_profile(name=profile_name)
    return await profiles.add_stereotype(
        profile.id,
        name=name,
        base_type=base,
        colour="#ffe0e0",
        icon_key=IconKey.DATABASE,
        properties=props,
    )


async def an_element(service: ArchitectureService, **kw: object):
    return await service.create_element(
        element_type=kw.pop("element_type", ElementType.APPLICATION_COMPONENT),
        name="Facturation",
        **kw,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
class TestProfiles:
    async def test_a_profile_name_is_unique(self, profile_service: ProfileService) -> None:
        await profile_service.create_profile(name="SI")

        with pytest.raises(DuplicateProfileError):
            await profile_service.create_profile(name="SI")

    async def test_an_exported_profile_can_be_imported_back_under_another_name(
        self, profile_service: ProfileService
    ) -> None:
        await a_stereotype(profile_service, TIER, OWNER)
        (profile,) = await profile_service.list_profiles()
        document = await profile_service.export_profile(profile.id)

        with pytest.raises(DuplicateProfileError):
            await profile_service.import_profile(document)
        imported = await profile_service.import_profile({**document, "name": "SI (copie)"})

        assert [s.name for s in imported.stereotypes] == ["database"]

    async def test_a_reader_lists_but_never_writes(self, profile_service: ProfileService) -> None:
        with acting_as(a_reader()):
            assert await profile_service.list_stereotypes() == ()
            with pytest.raises(NotAuthorisedError):
                await profile_service.import_profile(
                    {"profile_format": 1, "name": "X", "description": "", "stereotypes": []}
                )


@pytest.mark.asyncio
class TestApplying:
    async def test_a_stereotype_is_applied_to_an_element_of_its_base_type_and_shown_on_it(
        self, profile_service: ProfileService, service: ArchitectureService
    ) -> None:
        stereotype = await a_stereotype(profile_service, TIER)
        element = await an_element(service)

        applied = await profile_service.apply_stereotype(element.id, stereotype.id)

        assert [ref.name for ref in applied.stereotypes] == ["database"]
        assert [r.name for r in (await service.get_element(element.id)).stereotypes] == ["database"]
        listed = await service.list_elements(ElementFilter())
        assert [r.name for r in listed[0].stereotypes] == ["database"]

    async def test_a_stereotype_of_another_type_is_refused_with_both_types_named(
        self, profile_service: ProfileService, service: ArchitectureService
    ) -> None:
        stereotype = await a_stereotype(profile_service)
        node = await an_element(service, element_type=ElementType.NODE)

        with pytest.raises(ValueError, match=r"application_component.*node"):
            await profile_service.apply_stereotype(node.id, stereotype.id)

    async def test_the_values_already_there_are_revalidated(
        self, profile_service: ProfileService, service: ArchitectureService
    ) -> None:
        stereotype = await a_stereotype(profile_service, TIER)
        element = await an_element(service, properties={"tier": "9"})

        with pytest.raises(ValueError, match="tier"):
            await profile_service.apply_stereotype(element.id, stereotype.id)
        assert (await service.get_element(element.id)).stereotypes == ()

    async def test_the_properties_are_judged_by_the_definitions_as_locked_not_as_read_before(
        self,
        profile_service: ProfileService,
        service: ArchitectureService,
        profiles: InMemoryProfiles,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The service read the stereotype before an editor tightened it: the store's copy wins."""
        stereotype = await a_stereotype(profile_service, TIER)
        element = await an_element(service, properties={"tier": "9"})
        loose = replace(stereotype, properties=())

        async def read_before_the_change(_: object) -> object:
            return loose

        monkeypatch.setattr(profiles, "get_stereotype", read_before_the_change)

        with pytest.raises(ValueError, match="tier"):
            await profile_service.apply_stereotype(element.id, stereotype.id)
        assert (await service.get_element(element.id)).stereotypes == ()

    async def test_two_stereotypes_declaring_one_key_cannot_both_be_applied(
        self, profile_service: ProfileService, service: ArchitectureService
    ) -> None:
        first = await a_stereotype(profile_service, OWNER, name="un", profile_name="P1")
        second = await a_stereotype(profile_service, OWNER, name="deux", profile_name="P2")
        element = await an_element(service)

        with pytest.raises(ValueError, match="declared by both"):
            await profile_service.apply_stereotypes(element.id, [first.id, second.id])

    async def test_an_unknown_stereotype_is_a_not_found(
        self, profile_service: ProfileService, service: ArchitectureService
    ) -> None:
        element = await an_element(service)

        with pytest.raises(StereotypeNotFoundError):
            await profile_service.apply_stereotype(element.id, uuid4())

    async def test_removing_a_stereotype_leaves_the_properties_alone(
        self, profile_service: ProfileService, service: ArchitectureService
    ) -> None:
        stereotype = await a_stereotype(profile_service, TIER)
        element = await an_element(service, properties={"tier": "1"})
        await profile_service.apply_stereotype(element.id, stereotype.id)

        removed = await profile_service.remove_stereotype(element.id, stereotype.id)

        assert removed.stereotypes == ()
        assert dict(removed.properties) == {"tier": "1"}

    async def test_a_reader_cannot_apply(
        self, profile_service: ProfileService, service: ArchitectureService
    ) -> None:
        stereotype = await a_stereotype(profile_service)
        element = await an_element(service)

        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await profile_service.apply_stereotype(element.id, stereotype.id)


@pytest.mark.asyncio
class TestTheRuleBehindEveryDoor:
    async def test_updating_an_element_checks_the_stereotypes_it_already_carries(
        self, profile_service: ProfileService, service: ArchitectureService
    ) -> None:
        stereotype = await a_stereotype(profile_service, TIER, OWNER)
        element = await an_element(service)
        await profile_service.apply_stereotype(element.id, stereotype.id)

        with pytest.raises(ValueError, match="tier"):
            await service.update_element(element.id, properties={"tier": "9"})
        updated = await service.update_element(element.id, properties={"tier": "2", "libre": "ok"})

        assert dict(updated.properties) == {"tier": "2", "libre": "ok"}
        assert [r.name for r in updated.stereotypes] == ["database"]

    async def test_a_missing_required_property_never_blocks_a_write(
        self, profile_service: ProfileService, service: ArchitectureService
    ) -> None:
        stereotype = await a_stereotype(profile_service, OWNER)
        element = await an_element(service)
        await profile_service.apply_stereotype(element.id, stereotype.id)

        updated = await service.update_element(element.id, properties={})

        assert dict(updated.properties) == {}


@pytest.mark.asyncio
class TestChangingDefinitions:
    async def test_a_stereotype_in_use_cannot_be_deleted_and_the_count_is_named(
        self, profile_service: ProfileService, service: ArchitectureService
    ) -> None:
        stereotype = await a_stereotype(profile_service)
        for _ in range(2):
            element = await service.create_element(
                element_type=ElementType.APPLICATION_COMPONENT, name=f"A{_}"
            )
            await profile_service.apply_stereotype(element.id, stereotype.id)

        with pytest.raises(StereotypeInUseError, match="2 element"):
            await profile_service.delete_stereotype(stereotype.id)

    async def test_it_can_be_deleted_once_nothing_carries_it(
        self, profile_service: ProfileService, service: ArchitectureService
    ) -> None:
        stereotype = await a_stereotype(profile_service)
        element = await an_element(service)
        await profile_service.apply_stereotype(element.id, stereotype.id)
        await profile_service.remove_stereotype(element.id, stereotype.id)

        await profile_service.delete_stereotype(stereotype.id)

        assert await profile_service.list_stereotypes() == ()

    async def test_a_profile_holding_a_stereotype_in_use_cannot_be_deleted(
        self, profile_service: ProfileService, service: ArchitectureService
    ) -> None:
        stereotype = await a_stereotype(profile_service)
        element = await an_element(service)
        await profile_service.apply_stereotype(element.id, stereotype.id)

        with pytest.raises(StereotypeInUseError):
            await profile_service.delete_profile(stereotype.profile_id)

    async def test_the_base_type_of_a_stereotype_in_use_cannot_change(
        self, profile_service: ProfileService, service: ArchitectureService
    ) -> None:
        stereotype = await a_stereotype(profile_service)
        element = await an_element(service)
        await profile_service.apply_stereotype(element.id, stereotype.id)

        with pytest.raises(StereotypeInUseError, match="base type"):
            await profile_service.replace_stereotype(
                stereotype.id,
                name=stereotype.name,
                base_type=ElementType.NODE,
                colour=None,
                icon_key=IconKey.NONE,
                properties=(),
            )


@pytest.mark.asyncio
class TestTheTrailAndTheReview:
    async def test_each_write_leaves_one_event_and_names_no_value(
        self,
        profile_service: ProfileService,
        service: ArchitectureService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        stereotype = await a_stereotype(profile_service, TIER)
        element = await an_element(service, properties={"tier": "1", "note": "secret-value"})
        audit_log.events.clear()

        await profile_service.apply_stereotype(element.id, stereotype.id)
        await profile_service.remove_stereotype(element.id, stereotype.id)
        await profile_service.delete_stereotype(stereotype.id)

        assert [e.action for e in audit_log.events] == [
            AuditAction.ELEMENT_STEREOTYPES_CHANGED,
            AuditAction.ELEMENT_STEREOTYPES_CHANGED,
            AuditAction.PROFILE_UPDATED,
        ]
        assert audit_log.events[0].after == {"stereotypes": ["database"]}
        assert audit_log.events[1].before == {"stereotypes": ["database"]}
        assert "secret-value" not in str(audit_log.events)

    async def test_an_approved_element_takes_no_label_until_an_approver_reopens_it(
        self, profile_service: ProfileService, service: ArchitectureService
    ) -> None:
        stereotype = await a_stereotype(profile_service)
        element = await an_element(service)
        await service.transition_element(element.id, Transition.SUBMIT)
        with acting_as(an_approver()):
            await service.transition_element(
                element.id, Transition.APPROVE, expected_revision=element.revision
            )

        with pytest.raises(ElementLockedError):
            await profile_service.apply_stereotype(element.id, stereotype.id)

    async def test_applying_a_label_is_a_change_of_the_element_for_the_review(
        self, profile_service: ProfileService, service: ArchitectureService
    ) -> None:
        stereotype = await a_stereotype(profile_service)
        element = await an_element(service)

        applied = await profile_service.apply_stereotype(element.id, stereotype.id)

        assert applied.revision == element.revision + 1


@pytest.mark.asyncio
class TestTheLibraryOfProfilesIsBounded:
    async def test_a_profile_beyond_the_cap_is_refused_until_one_is_deleted(
        self, profile_service: ProfileService
    ) -> None:
        for i in range(MAX_PROFILES):
            await profile_service.create_profile(name=f"P{i}")

        with pytest.raises(ProfileLimitError):
            await profile_service.create_profile(name="one too many")

        first = (await profile_service.list_profiles())[0]
        await profile_service.delete_profile(first.id)
        await profile_service.create_profile(name="room again")
