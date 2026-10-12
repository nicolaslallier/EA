"""Profiles against a real PostgreSQL: the whole tree in one transaction, RESTRICT, cascades."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType
from ea.domain.errors import DuplicateProfileError, ProfileLimitError, StereotypeInUseError
from ea.domain.model import Element
from ea.domain.profiles import (
    IconKey,
    Profile,
    PropertyKind,
    Stereotype,
    StereotypeProperty,
    validate_stereotype_properties,
)
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.profile_store import PostgresProfileRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def a_profile(name: str = "SI") -> Profile:
    profile_id = uuid4()
    stereotype = Stereotype(
        id=uuid4(),
        profile_id=profile_id,
        name="database",
        base_type=ElementType.APPLICATION_COMPONENT,
        colour="#ffe0e0",
        icon_key=IconKey.DATABASE,
        properties=(
            StereotypeProperty(key="owner", label="Responsable", required=True),
            StereotypeProperty(
                key="tier", label="Niveau", kind=PropertyKind.ENUM, allowed_values=("1", "2")
            ),
        ),
    )
    return Profile(
        id=profile_id,
        name=name,
        description="",
        created_at=NOW,
        updated_at=NOW,
        stereotypes=(stereotype,),
    )


@pytest.fixture
def profiles(engine_at_head: AsyncEngine) -> PostgresProfileRepository:
    return PostgresProfileRepository(create_session_factory(engine_at_head))


@pytest.fixture
def graph(engine_at_head: AsyncEngine) -> PostgresArchitectureRepository:
    return PostgresArchitectureRepository(create_session_factory(engine_at_head))


async def test_a_profile_round_trips_with_its_stereotypes_and_their_properties_in_order(
    profiles: PostgresProfileRepository,
) -> None:
    stored = await profiles.add_profile(a_profile(), audit=())

    read = await profiles.get_profile(stored.id)

    assert read == stored
    assert [p.key for p in read.stereotypes[0].properties] == ["owner", "tier"]  # type: ignore[union-attr]


async def test_a_profile_name_is_unique_and_the_store_stays_usable(
    profiles: PostgresProfileRepository,
) -> None:
    await profiles.add_profile(a_profile("SI"), audit=())

    with pytest.raises(DuplicateProfileError):
        await profiles.add_profile(a_profile("SI"), audit=())
    await profiles.add_profile(a_profile("Autre"), audit=())

    assert [p.name for p in await profiles.list_profiles()] == ["Autre", "SI"]


async def test_replacing_a_stereotype_replaces_its_properties_atomically(
    profiles: PostgresProfileRepository,
) -> None:
    profile = await profiles.add_profile(a_profile(), audit=())
    original = profile.stereotypes[0]
    changed = Stereotype(
        id=original.id,
        profile_id=original.profile_id,
        name="base de données",
        base_type=original.base_type,
        colour=None,
        icon_key=IconKey.NONE,
        properties=(StereotypeProperty(key="env", label="Environnement"),),
    )

    await profiles.replace_stereotype(changed, audit=())

    read = await profiles.get_stereotype(original.id)
    assert read == changed


async def test_tags_are_listed_for_many_elements_in_one_call_and_replaced_as_a_set(
    profiles: PostgresProfileRepository, graph: PostgresArchitectureRepository
) -> None:
    profile = await profiles.add_profile(a_profile(), audit=())
    stereotype = profile.stereotypes[0]
    one = await graph.add_element(
        Element.create(element_type=ElementType.APPLICATION_COMPONENT, name="A", now=NOW), audit=()
    )
    two = await graph.add_element(
        Element.create(element_type=ElementType.APPLICATION_COMPONENT, name="B", now=NOW), audit=()
    )

    await profiles.set_for_element(one.id, [stereotype.id], audit=())
    refs = await profiles.refs_of([one.id, two.id])

    assert refs[one.id] == (stereotype.ref,)
    assert two.id not in refs or refs[two.id] == ()
    assert (await profiles.stereotypes_of(one.id))[0].properties == stereotype.properties
    assert await profiles.applied_counts([stereotype.id]) == {stereotype.id: 1}

    await profiles.set_for_element(one.id, [], audit=())
    assert await profiles.refs_of([one.id]) == {}


async def test_a_stereotype_in_use_cannot_be_deleted_but_an_element_takes_its_tags_with_it(
    profiles: PostgresProfileRepository, graph: PostgresArchitectureRepository
) -> None:
    profile = await profiles.add_profile(a_profile(), audit=())
    stereotype = profile.stereotypes[0]
    element = await graph.add_element(
        Element.create(element_type=ElementType.APPLICATION_COMPONENT, name="A", now=NOW), audit=()
    )
    await profiles.set_for_element(element.id, [stereotype.id], audit=())

    with pytest.raises(StereotypeInUseError):
        await profiles.delete_stereotype(stereotype.id, audit=())
    assert await profiles.get_stereotype(stereotype.id) is not None

    await graph.delete_element(element.id, audit=())

    assert await profiles.applied_counts([stereotype.id]) == {}
    assert await profiles.delete_stereotype(stereotype.id, audit=()) is True


async def test_deleting_an_unused_profile_takes_its_stereotypes(
    profiles: PostgresProfileRepository,
) -> None:
    profile = await profiles.add_profile(a_profile(), audit=())

    assert await profiles.delete_profile(profile.id, audit=()) is True

    assert await profiles.list_stereotypes() == ()


async def test_the_base_type_of_an_applied_stereotype_cannot_change_even_in_a_race(
    profiles: PostgresProfileRepository, graph: PostgresArchitectureRepository
) -> None:
    profile = await profiles.add_profile(a_profile(), audit=())
    original = profile.stereotypes[0]
    element = await graph.add_element(
        Element.create(element_type=ElementType.APPLICATION_COMPONENT, name="A", now=NOW), audit=()
    )
    await profiles.set_for_element(element.id, [original.id], audit=())
    retyped = Stereotype(
        id=original.id,
        profile_id=original.profile_id,
        name=original.name,
        base_type=ElementType.NODE,
        colour=None,
        icon_key=IconKey.NONE,
    )

    with pytest.raises(StereotypeInUseError):
        await profiles.replace_stereotype(retyped, audit=())


async def test_a_label_of_another_type_is_refused_inside_the_transaction(
    profiles: PostgresProfileRepository, graph: PostgresArchitectureRepository
) -> None:
    profile = await profiles.add_profile(a_profile(), audit=())
    node = await graph.add_element(
        Element.create(element_type=ElementType.NODE, name="N", now=NOW), audit=()
    )

    with pytest.raises(ValueError, match="applies to"):
        await profiles.set_for_element(node.id, [profile.stereotypes[0].id], audit=())
    assert await profiles.refs_of([node.id]) == {}


async def test_a_label_is_a_write_to_the_element_a_revision_and_the_guard(
    profiles: PostgresProfileRepository, graph: PostgresArchitectureRepository
) -> None:
    profile = await profiles.add_profile(a_profile(), audit=())
    element = await graph.add_element(
        Element.create(element_type=ElementType.APPLICATION_COMPONENT, name="A", now=NOW), audit=()
    )

    await profiles.set_for_element(element.id, [profile.stereotypes[0].id], audit=())

    read = await graph.get_element(element.id)
    assert read is not None and read.revision == element.revision + 1


async def test_two_concurrent_profiles_at_the_edge_cannot_both_get_in(
    profiles: PostgresProfileRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ea.repositories.profile_store.MAX_PROFILES", 2)
    await profiles.add_profile(a_profile("first"), audit=())

    outcomes = await asyncio.gather(
        profiles.add_profile(a_profile("a"), audit=()),
        profiles.add_profile(a_profile("b"), audit=()),
        return_exceptions=True,
    )

    assert sum(isinstance(o, ProfileLimitError) for o in outcomes) == 1
    assert len(await profiles.list_profiles()) == 2


async def test_the_properties_are_checked_on_the_row_as_locked_not_as_read_before(
    profiles: PostgresProfileRepository,
    graph: PostgresArchitectureRepository,
    engine_at_head: AsyncEngine,
) -> None:
    """A concurrent edit of the properties must be seen by the check, not slip past it."""
    profile = await profiles.add_profile(a_profile(), audit=())
    stereotype = profile.stereotypes[0]
    element = await graph.add_element(
        Element.create(
            element_type=ElementType.APPLICATION_COMPONENT,
            name="A",
            properties={"tier": "1"},
            now=NOW,
        ),
        audit=(),
    )
    editor = await engine_at_head.connect()
    transaction = await editor.begin()
    # Somebody sets a value the stereotype forbids and has not committed yet.
    await editor.execute(
        text('UPDATE elements SET properties = \'{"tier": "9"}\'::jsonb WHERE id = :id'),
        {"id": element.id},
    )

    applying = asyncio.ensure_future(
        profiles.set_for_element(
            element.id,
            [stereotype.id],
            audit=(),
            check=lambda properties: validate_stereotype_properties(properties, [stereotype]),
        )
    )
    await asyncio.sleep(0.5)  # the application is now waiting on the element, or has gone past it
    await transaction.commit()
    await editor.close()

    with pytest.raises(ValueError, match="tier"):
        await applying
    assert await profiles.refs_of([element.id]) == {}
