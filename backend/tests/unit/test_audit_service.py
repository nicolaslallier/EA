"""Who, when, through which door — and who may read the trail."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.core.logging import request_id
from ea.domain.audit import AuditAction, AuditChannel, AuditFilter, EntityKind
from ea.domain.errors import NotAuthenticatedError
from ea.services.audit import AuditService, AuditTrail
from ea.services.caller import acting_through
from tests.conftest import FIXED_NOW, InMemoryAuditLog


class TestTheTrail:
    def test_an_event_carries_the_caller_the_clock_the_request_and_the_channel(self) -> None:
        trail = AuditTrail(lambda: FIXED_NOW)
        entity = uuid4()
        token = request_id.set("req-42")
        try:
            with acting_through(AuditChannel.MCP):
                event = trail.event(
                    AuditAction.ELEMENT_CREATED, entity_id=entity, after={"name": "A"}
                )
        finally:
            request_id.reset(token)

        assert (event.actor_sub, event.actor_name) == ("editor-1", "editor")
        assert event.at == FIXED_NOW
        assert event.request_id == "req-42"
        assert event.channel is AuditChannel.MCP
        assert event.entity_kind is EntityKind.ELEMENT
        assert event.entity_id == entity

    def test_outside_a_request_the_id_is_a_dash_and_the_channel_is_the_system(self) -> None:
        event = AuditTrail(lambda: FIXED_NOW).event(AuditAction.ELEMENT_DELETED, entity_id=None)

        assert event.request_id == "-"
        assert event.channel is AuditChannel.SYSTEM

    @pytest.mark.usefixtures("nobody_calling")
    def test_nobody_calling_means_no_event_at_all(self) -> None:
        with pytest.raises(NotAuthenticatedError):
            AuditTrail(lambda: FIXED_NOW).event(AuditAction.ELEMENT_CREATED, entity_id=None)


@pytest.mark.asyncio
class TestReadingTheTrail:
    async def test_any_authenticated_caller_may_read(self, audit_log: InMemoryAuditLog) -> None:
        service = AuditService(audit_log)
        audit_log.append(
            [AuditTrail(lambda: FIXED_NOW).event(AuditAction.ELEMENT_CREATED, entity_id=None)]
        )

        page = await service.list_events(AuditFilter())

        assert len(page.events) == 1

    @pytest.mark.usefixtures("nobody_calling")
    async def test_an_anonymous_reader_is_refused(self, audit_log: InMemoryAuditLog) -> None:
        with pytest.raises(NotAuthenticatedError):
            await AuditService(audit_log).list_events(AuditFilter())

    async def test_a_page_larger_than_the_bound_is_refused(
        self, audit_log: InMemoryAuditLog
    ) -> None:
        with pytest.raises(ValueError, match="at most 200"):
            await AuditService(audit_log).list_events(AuditFilter(limit=201))
        with pytest.raises(ValueError, match="at least 1"):
            await AuditService(audit_log).list_events(AuditFilter(limit=0))
