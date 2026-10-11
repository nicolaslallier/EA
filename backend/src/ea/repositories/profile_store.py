"""The PostgreSQL implementation of `ProfileRepository` - see docs/adr/0064.

Like the diagram store it takes the session factory and opens its units of work
through `ea.db.unit_of_work`. A profile and its stereotypes are written in one
transaction; replacing a stereotype replaces its properties in the same one. A
name already taken, or a stereotype still applied, is recognised by the **name of
the constraint**, never by the message. Every write inserts its audit events in its
own transaction, built from the profile as that transaction read it.

Applying stereotypes to an element is a write to the element (docs/adr/0048): its row
is locked, put to the one guard every way into an element asks, and touched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from ea.db.models.architecture import ElementRecord
from ea.db.models.profile import (
    ElementStereotypeRecord,
    ProfileRecord,
    PropertyRecord,
    StereotypeRecord,
)
from ea.db.unit_of_work import reading, work
from ea.domain.approval import WriteKind, guard_element_write
from ea.domain.archimate import ElementType
from ea.domain.audit import events_for
from ea.domain.errors import (
    DuplicateProfileError,
    ElementNotFoundError,
    ProfileNotFoundError,
    StereotypeInUseError,
    StereotypeNotFoundError,
)
from ea.domain.model import StereotypeRef
from ea.domain.profiles import (
    IconKey,
    Profile,
    PropertyKind,
    Stereotype,
    StereotypeProperty,
)
from ea.repositories.architecture_store import author_of, constraint_of, element_from_row, touch
from ea.repositories.audit_store import write_events

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ea.domain.audit import AuditOf

_TAKEN: Final = {"uq_profiles_name", "uq_profile_stereotypes_profile_id_name"}
_IN_USE: Final = "fk_element_stereotypes_stereotype_id_profile_stereotypes"


def _refused(error: IntegrityError, what: str) -> Exception:
    """A taken name or an applied stereotype is a typed refusal; anything else is not ours."""
    name = constraint_of(error)
    if name in _TAKEN:
        return DuplicateProfileError(f"{what} is already taken")
    if name == _IN_USE:
        return StereotypeInUseError(f"{what} is applied to elements")
    return error


def _property(row: PropertyRecord) -> StereotypeProperty:
    return StereotypeProperty(
        key=row.key,
        label=row.label,
        required=row.required,
        kind=PropertyKind(row.kind),
        allowed_values=tuple(row.allowed_values),
        template=row.template,
        help=row.help,
    )


def _stereotype(row: StereotypeRecord, props: Sequence[PropertyRecord]) -> Stereotype:
    return Stereotype(
        id=row.id,
        profile_id=row.profile_id,
        name=row.name,
        base_type=ElementType(row.base_type),
        colour=row.colour,
        icon_key=IconKey(row.icon_key),
        properties=tuple(_property(p) for p in sorted(props, key=lambda p: p.position)),
    )


def _rows(stereotype: Stereotype) -> tuple[StereotypeRecord, list[PropertyRecord]]:
    return (
        StereotypeRecord(
            id=stereotype.id,
            profile_id=stereotype.profile_id,
            name=stereotype.name,
            base_type=stereotype.base_type.value,
            colour=stereotype.colour,
            icon_key=stereotype.icon_key.value,
        ),
        [
            PropertyRecord(
                stereotype_id=stereotype.id,
                key=p.key,
                position=index,
                label=p.label,
                required=p.required,
                kind=p.kind.value,
                allowed_values=list(p.allowed_values),
                template=p.template,
                help=p.help,
            )
            for index, p in enumerate(stereotype.properties)
        ],
    )


def _ref(row: StereotypeRecord) -> StereotypeRef:
    return StereotypeRef(id=row.id, name=row.name, colour=row.colour, icon_key=row.icon_key)


async def _stereotypes(
    session: AsyncSession,
    *,
    profile_id: UUID | None = None,
    ids: Sequence[UUID] | None = None,
) -> tuple[Stereotype, ...]:
    statement = select(StereotypeRecord).order_by(StereotypeRecord.name, StereotypeRecord.id)
    if profile_id is not None:
        statement = statement.where(StereotypeRecord.profile_id == profile_id)
    if ids is not None:
        statement = statement.where(StereotypeRecord.id.in_(ids))
    rows = list(await session.scalars(statement))
    props = list(
        await session.scalars(
            select(PropertyRecord).where(PropertyRecord.stereotype_id.in_([r.id for r in rows]))
        )
    )
    return tuple(_stereotype(row, [p for p in props if p.stereotype_id == row.id]) for row in rows)


def _profile(row: ProfileRecord, stereotypes: Sequence[Stereotype]) -> Profile:
    return Profile(
        id=row.id,
        name=row.name,
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
        stereotypes=tuple(s for s in stereotypes if s.profile_id == row.id),
    )


async def _profile_of(session: AsyncSession, profile_id: UUID, *, lock: bool = False) -> Profile:
    """The profile as this transaction sees it - locked when the caller is about to change it."""
    row = await session.get(ProfileRecord, profile_id, with_for_update=lock, populate_existing=lock)
    if row is None:
        msg = f"no profile with id {profile_id}"
        raise ProfileNotFoundError(msg)
    return _profile(row, await _stereotypes(session, profile_id=profile_id))


class PostgresProfileRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_profiles(self) -> tuple[Profile, ...]:
        async with reading(self._sessions) as session:
            rows = list(await session.scalars(select(ProfileRecord).order_by(ProfileRecord.name)))
            stereotypes = await _stereotypes(session)
            return tuple(_profile(row, stereotypes) for row in rows)

    async def get_profile(self, profile_id: UUID) -> Profile | None:
        async with reading(self._sessions) as session:
            row = await session.get(ProfileRecord, profile_id)
            if row is None:
                return None
            return _profile(row, await _stereotypes(session, profile_id=profile_id))

    async def add_profile(self, profile: Profile, *, audit: AuditOf[Profile]) -> Profile:
        events = events_for(audit, profile)
        async with work(self._sessions) as session:
            session.add(
                ProfileRecord(
                    id=profile.id,
                    name=profile.name,
                    description=profile.description,
                    created_at=profile.created_at,
                    updated_at=profile.updated_at,
                )
            )
            try:
                await session.flush()
                for stereotype in profile.stereotypes:
                    record, props = _rows(stereotype)
                    session.add(record)
                    await session.flush()
                    session.add_all(props)
                await session.flush()
            except IntegrityError as error:
                raise _refused(error, f"the profile name {profile.name!r}") from error
            write_events(session, events)
        return profile

    async def save_profile(self, profile: Profile, *, audit: AuditOf[Profile]) -> Profile:
        async with work(self._sessions) as session:
            before = await _profile_of(session, profile.id, lock=True)
            row = await session.get(ProfileRecord, profile.id)
            if row is None:  # the locked read above found it: only a concurrent delete
                raise ProfileNotFoundError(str(profile.id))
            row.name = profile.name
            row.description = profile.description
            row.updated_at = profile.updated_at
            try:
                await session.flush()
            except IntegrityError as error:
                raise _refused(error, f"the profile name {profile.name!r}") from error
            write_events(session, events_for(audit, before))
        return profile

    async def delete_profile(self, profile_id: UUID, *, audit: AuditOf[Profile]) -> bool:
        async with work(self._sessions) as session:
            try:
                before = await _profile_of(session, profile_id, lock=True)
            except ProfileNotFoundError:
                return False
            events = events_for(audit, before)
            try:
                await session.execute(delete(ProfileRecord).where(ProfileRecord.id == profile_id))
            except IntegrityError as error:  # RESTRICT: a stereotype of it is still applied
                raise _refused(error, f"the profile {before.name!r}") from error
            write_events(session, events)
        return True

    async def list_stereotypes(self) -> tuple[Stereotype, ...]:
        async with reading(self._sessions) as session:
            return await _stereotypes(session)

    async def get_stereotype(self, stereotype_id: UUID) -> Stereotype | None:
        async with reading(self._sessions) as session:
            found = await _stereotypes(session, ids=[stereotype_id])
            return found[0] if found else None

    async def add_stereotype(
        self, stereotype: Stereotype, *, audit: AuditOf[Profile]
    ) -> Stereotype:
        async with work(self._sessions) as session:
            before = await _profile_of(session, stereotype.profile_id, lock=True)
            record, props = _rows(stereotype)
            session.add(record)
            try:
                await session.flush()
            except IntegrityError as error:
                raise _refused(error, f"the stereotype name {stereotype.name!r}") from error
            session.add_all(props)
            await session.flush()
            write_events(session, events_for(audit, before))
        return stereotype

    async def replace_stereotype(
        self, stereotype: Stereotype, *, audit: AuditOf[Profile]
    ) -> Stereotype:
        async with work(self._sessions) as session:
            row = await session.get(StereotypeRecord, stereotype.id, with_for_update=True)
            if row is None:
                msg = f"no stereotype with id {stereotype.id}"
                raise StereotypeNotFoundError(msg)
            before = await _profile_of(session, row.profile_id, lock=True)
            if row.base_type != stereotype.base_type.value:
                applied = await session.scalar(
                    select(func.count()).where(ElementStereotypeRecord.stereotype_id == row.id)
                )
                if applied:
                    msg = f"the stereotype {row.name!r} is applied to {applied} element(s)"
                    raise StereotypeInUseError(msg)
            row.name = stereotype.name
            row.base_type = stereotype.base_type.value
            row.colour = stereotype.colour
            row.icon_key = stereotype.icon_key.value
            await session.execute(
                delete(PropertyRecord).where(PropertyRecord.stereotype_id == stereotype.id)
            )
            try:
                await session.flush()
            except IntegrityError as error:
                raise _refused(error, f"the stereotype name {stereotype.name!r}") from error
            session.add_all(_rows(stereotype)[1])
            await session.flush()
            write_events(session, events_for(audit, before))
        return stereotype

    async def delete_stereotype(self, stereotype_id: UUID, *, audit: AuditOf[Profile]) -> bool:
        """RESTRICT: a stereotype still applied is refused by the database, not by a lookup."""
        async with work(self._sessions) as session:
            row = await session.get(StereotypeRecord, stereotype_id, with_for_update=True)
            if row is None:
                return False
            before = await _profile_of(session, row.profile_id, lock=True)
            events = events_for(audit, before)
            try:
                await session.execute(
                    delete(StereotypeRecord).where(StereotypeRecord.id == stereotype_id)
                )
            except IntegrityError as error:
                raise _refused(error, f"the stereotype {row.name!r}") from error
            write_events(session, events)
        return True

    async def applied_counts(self, stereotype_ids: Sequence[UUID]) -> dict[UUID, int]:
        statement = (
            select(ElementStereotypeRecord.stereotype_id, func.count())
            .where(ElementStereotypeRecord.stereotype_id.in_(stereotype_ids))
            .group_by(ElementStereotypeRecord.stereotype_id)
        )
        async with reading(self._sessions) as session:
            return {row[0]: row[1] for row in await session.execute(statement)}

    async def set_for_element(
        self,
        element_id: UUID,
        stereotype_ids: Sequence[UUID],
        *,
        audit: AuditOf[tuple[StereotypeRef, ...]],
    ) -> None:
        wanted = list(dict.fromkeys(stereotype_ids))
        async with work(self._sessions) as session:
            # The element first, locked the way every write to it is (docs/adr/0048), then
            # the guard on the row as it is *now*, in the transaction that writes.
            element = await session.get(
                ElementRecord,
                element_id,
                with_for_update={"key_share": True},
                populate_existing=True,
            )
            if element is None:
                msg = f"no element with id {element_id}"
                raise ElementNotFoundError(msg)
            guard_element_write(element_from_row(element), WriteKind.STEREOTYPES)
            # The definitions are held while the label goes on, so none is re-typed or removed
            # between the service's check and this write.
            definitions = {
                row.id: row
                for row in await session.scalars(
                    select(StereotypeRecord)
                    .where(StereotypeRecord.id.in_(wanted))
                    .order_by(StereotypeRecord.id)
                    .with_for_update(key_share=True)
                )
            }
            for stereotype_id in wanted:
                found = definitions.get(stereotype_id)
                if found is None:
                    msg = f"no stereotype with id {stereotype_id}"
                    raise StereotypeNotFoundError(msg)
                if found.base_type != element.element_type:
                    msg = (
                        f"stereotype {found.name!r} applies to {found.base_type}, "
                        f"not to {element.element_type}"
                    )
                    raise ValueError(msg)
            carried = await self._refs(session, [element_id])
            events = events_for(audit, carried.get(element_id, ()))
            await session.execute(
                delete(ElementStereotypeRecord).where(
                    ElementStereotypeRecord.element_id == element_id
                )
            )
            session.add_all(
                ElementStereotypeRecord(element_id=element_id, stereotype_id=sid) for sid in wanted
            )
            await session.flush()
            write_events(session, events)
            await touch(session, [element_id], author_of(events))

    @staticmethod
    async def _refs(
        session: AsyncSession, element_ids: Sequence[UUID]
    ) -> dict[UUID, tuple[StereotypeRef, ...]]:
        statement = (
            select(ElementStereotypeRecord.element_id, StereotypeRecord)
            .join(StereotypeRecord, StereotypeRecord.id == ElementStereotypeRecord.stereotype_id)
            .where(ElementStereotypeRecord.element_id.in_(element_ids))
            .order_by(StereotypeRecord.name)
        )
        found: dict[UUID, list[StereotypeRef]] = {}
        for element_id, row in await session.execute(statement):
            found.setdefault(element_id, []).append(_ref(row))
        return {key: tuple(value) for key, value in found.items()}

    async def refs_of(self, element_ids: Sequence[UUID]) -> dict[UUID, tuple[StereotypeRef, ...]]:
        async with reading(self._sessions) as session:
            return await self._refs(session, element_ids)

    async def stereotypes_of(self, element_id: UUID) -> tuple[Stereotype, ...]:
        async with reading(self._sessions) as session:
            ids = list(
                await session.scalars(
                    select(ElementStereotypeRecord.stereotype_id).where(
                        ElementStereotypeRecord.element_id == element_id
                    )
                )
            )
            return await _stereotypes(session, ids=ids) if ids else ()
