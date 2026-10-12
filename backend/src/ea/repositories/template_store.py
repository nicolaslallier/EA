"""The PostgreSQL implementation of `TemplateRepository` - a body in, a body out (docs/adr/0072).

Every write opens its unit through `ea.db.unit_of_work` and inserts its audit events in the
same transaction. A body read back goes through the same validators that wrote it, so a row
somebody edited by hand cannot reach a use case as a skeleton the domain would refuse.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from ea.db.models.diagram_template import DiagramTemplateRecord
from ea.db.unit_of_work import reading, work
from ea.domain.audit import events_for
from ea.domain.diagram_templates import MAX_TEMPLATES, DiagramTemplate, body_to_json, parse_body
from ea.domain.errors import DuplicateTemplateError, TemplateLimitError, TemplateNotFoundError
from ea.repositories.audit_store import write_events

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.engine import CursorResult
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ea.domain.audit import AuditOf


#: Serialises the count-then-insert of `add`: two writers cannot both see 199 and both insert.
_LIBRARY_LOCK = 0x45_41_00_06


def _to_template(row: DiagramTemplateRecord) -> DiagramTemplate:
    return DiagramTemplate(
        id=row.id,
        name=row.name,
        description=row.description,
        body=parse_body(row.body),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _taken(template: DiagramTemplate) -> DuplicateTemplateError:
    return DuplicateTemplateError(f"a template is already named {template.name!r}")


class PostgresTemplateRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_all(self) -> tuple[DiagramTemplate, ...]:
        statement = select(DiagramTemplateRecord).order_by(DiagramTemplateRecord.name)
        async with reading(self._sessions) as session:
            return tuple(_to_template(row) for row in await session.scalars(statement))

    async def get(self, template_id: UUID) -> DiagramTemplate | None:
        async with reading(self._sessions) as session:
            row = await session.get(DiagramTemplateRecord, template_id)
            return _to_template(row) if row is not None else None

    async def add(
        self, template: DiagramTemplate, *, audit: AuditOf[DiagramTemplate]
    ) -> DiagramTemplate:
        events = events_for(audit, template)
        async with work(self._sessions) as session:
            await session.execute(select(func.pg_advisory_xact_lock(_LIBRARY_LOCK)))
            held = await session.scalar(select(func.count()).select_from(DiagramTemplateRecord))
            if (held or 0) >= MAX_TEMPLATES:
                msg = f"the library holds {MAX_TEMPLATES} templates at most"
                raise TemplateLimitError(msg)
            session.add(
                DiagramTemplateRecord(
                    id=template.id,
                    name=template.name,
                    description=template.description,
                    body=body_to_json(template.body),
                    created_at=template.created_at,
                    updated_at=template.updated_at,
                )
            )
            try:
                await session.flush()
            except IntegrityError as error:
                raise _taken(template) from error
            write_events(session, events)
        return template

    async def save(
        self, template: DiagramTemplate, *, audit: AuditOf[DiagramTemplate]
    ) -> DiagramTemplate:
        async with work(self._sessions) as session:
            row = await session.get(DiagramTemplateRecord, template.id, with_for_update=True)
            if row is None:
                msg = f"no template with id {template.id}"
                raise TemplateNotFoundError(msg)
            events = events_for(audit, _to_template(row))
            row.name = template.name
            row.description = template.description
            row.body = body_to_json(template.body)
            row.updated_at = template.updated_at
            try:
                await session.flush()
            except IntegrityError as error:
                raise _taken(template) from error
            write_events(session, events)
        return template

    async def delete(self, template_id: UUID, *, audit: AuditOf[DiagramTemplate]) -> bool:
        async with work(self._sessions) as session:
            row = await session.get(DiagramTemplateRecord, template_id, with_for_update=True)
            if row is None:
                return False
            events = events_for(audit, _to_template(row))
            deleted = await session.execute(
                delete(DiagramTemplateRecord).where(DiagramTemplateRecord.id == template_id)
            )
            write_events(session, events)
        return bool(cast("CursorResult[Any]", deleted).rowcount)
