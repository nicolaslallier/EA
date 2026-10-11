"""Who an event is attributed to is decided by the server, never by the caller (docs/adr/0044).

The actor, the door, the request and the moment of an event come from the
verified `Caller`, the adapter that received the call, the logging context and
the injected clock. Nothing a caller sends — an argument, a property, a header
— reaches any of them.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from ea.api.middleware import _client_identifier
from ea.domain.archimate import ElementType as E
from ea.domain.audit import AuditAction, AuditChannel
from ea.domain.auth import LOCAL_DEVELOPER, SYSTEM
from ea.domain.errors import NotAuthenticatedError
from ea.mcp.errors import speaking_plainly
from ea.repositories.audit_store import write_events
from ea.services.architecture import ArchitectureService
from ea.services.audit import AuditTrail
from ea.services.caller import acting_as, current_channel
from tests.conftest import FIXED_NOW, InMemoryAuditLog, InMemoryRepository, an_editor

pytestmark = pytest.mark.asyncio

SRC = Path(__file__).parents[2] / "src" / "ea"


async def test_the_trail_has_no_parameter_through_which_a_caller_could_name_an_actor() -> None:
    parameters = set(inspect.signature(AuditTrail.event).parameters) - {"self"}

    assert parameters == {"action", "entity_id", "before", "after", "correlation"}


async def test_only_the_trail_and_the_stores_ever_build_an_event() -> None:
    allowed = {"services/audit.py", "repositories/audit_store.py", "domain/audit.py"}
    builders = {
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if re.search(r"\bAuditEvent\(", path.read_text())
    }

    assert builders <= allowed, builders - allowed


async def test_properties_named_like_audit_fields_cannot_forge_an_event(
    service: ArchitectureService, audit_log: InMemoryAuditLog
) -> None:
    await service.create_element(
        element_type=E.NODE,
        name="n",
        properties={
            "actor_sub": "evil",
            "actor_name": "evil",
            "channel": "mcp",
            "request_id": "forged",
            "at": "1999-01-01T00:00:00+00:00",
            "action": "element.deleted",
            "correlation": "restore:x",
        },
    )

    (event,) = audit_log.events
    assert (event.actor_sub, event.actor_name) == ("editor-1", "editor")
    assert event.channel is AuditChannel.SYSTEM
    assert event.request_id == "-"
    assert event.at == FIXED_NOW
    assert event.action is AuditAction.ELEMENT_CREATED
    assert event.correlation is None
    # What a client wrote stays under `properties`, never beside the fixed keys.
    assert event.after is not None
    assert set(event.after) == {
        "id",
        "element_type",
        "layer",
        "aspect",
        "name",
        "description",
        "documentation",
        "package_id",
        "properties",
        "approval_state",
        "locked",
        "approved_by_sub",
        "approved_at",
        "created_at",
        "updated_at",
    }


@pytest.mark.parametrize(
    "header",
    ["bad id", "line\nbreak", "x" * 65, "", "é"],
)
async def test_a_client_request_id_that_is_not_plain_is_ignored(header: str) -> None:
    scope = {"headers": [(b"x-request-id", header.encode("latin-1", "replace"))]}

    assert _client_identifier(scope) is None  # type: ignore[arg-type]


async def test_a_plain_client_request_id_is_kept_apart_for_the_logs() -> None:
    scope = {"headers": [(b"x-request-id", b"trace-1.a:b")]}

    assert _client_identifier(scope) == "trace-1.a:b"  # type: ignore[arg-type]


async def test_an_mcp_tool_runs_through_the_mcp_door_whatever_it_is_told() -> None:
    @speaking_plainly
    async def a_tool(channel: str = "http") -> AuditChannel:
        return current_channel.get()

    assert await a_tool(channel="http") is AuditChannel.MCP
    assert current_channel.get() is AuditChannel.SYSTEM  # and the door closes behind it


async def test_nobody_calling_means_no_write_and_no_event(
    service: ArchitectureService,
    audit_log: InMemoryAuditLog,
    repository: InMemoryRepository,
    nobody_calling: None,
) -> None:
    with pytest.raises(NotAuthenticatedError):
        await service.create_element(element_type=E.NODE, name="ghost")

    assert audit_log.events == []
    assert repository.elements == {}


async def test_the_system_is_only_the_system_when_it_says_so(
    service: ArchitectureService, audit_log: InMemoryAuditLog
) -> None:
    with acting_as(SYSTEM):
        await service.create_element(element_type=E.NODE, name="by-a-script")
    with acting_as(LOCAL_DEVELOPER):
        await service.create_element(element_type=E.NODE, name="by-debug")
    await service.create_element(element_type=E.NODE, name="by-the-default-editor")

    scripted, debug, editor = audit_log.events
    assert (scripted.actor_sub, scripted.channel) == ("ea-system", AuditChannel.SYSTEM)
    assert (debug.actor_sub, debug.actor_name) == ("local-developer", "local-developer")
    assert editor.actor_sub == "editor-1"


async def test_concurrent_calls_keep_each_their_own_actor(
    service: ArchitectureService, audit_log: InMemoryAuditLog
) -> None:
    async def create_as(index: int) -> None:
        with acting_as(an_editor(subject=f"sub-{index}", username=f"user-{index}")):
            await asyncio.sleep(0)  # let the others interleave
            await service.create_element(element_type=E.NODE, name=f"node-{index}")

    await asyncio.gather(*(create_as(index) for index in range(10)))

    assert len(audit_log.events) == 10
    for event in audit_log.events:
        assert event.after is not None
        index = event.after["name"].removeprefix("node-")
        assert (event.actor_sub, event.actor_name) == (f"sub-{index}", f"user-{index}")


async def test_an_event_without_an_actor_is_refused_before_it_is_written() -> None:
    added: list[Any] = []
    session = SimpleNamespace(add_all=added.extend)
    blank = AuditTrail(lambda: FIXED_NOW).event(AuditAction.ELEMENT_CREATED, entity_id=uuid4())
    for field in ("actor_sub", "actor_name", "request_id"):
        with pytest.raises(ValueError, match=field):
            write_events(session, [replace(blank, **{field: ""})])  # type: ignore[arg-type]

    assert added == []


@pytest.mark.parametrize("field", ["name", "description", "documentation", "property"])
async def test_a_nul_character_is_refused_as_an_input_error_before_any_write(
    field: str, service: ArchitectureService, audit_log: InMemoryAuditLog
) -> None:
    """PostgreSQL cannot hold a NUL in `text`/`jsonb`: it must be a 422, never a driver 500."""
    fields: dict[str, Any] = {"name": "n", "description": "", "documentation": ""}
    properties: dict[str, str] = {}
    if field == "property":
        properties["owner"] = "a\x00b"
    else:
        fields[field] = "a\x00b"

    with pytest.raises(ValueError, match="NUL"):
        await service.create_element(element_type=E.NODE, properties=properties, **fields)

    assert audit_log.events == []


async def test_a_nul_in_a_diagram_name_is_refused(diagram_service: Any) -> None:
    with pytest.raises(ValueError, match="NUL"):
        await diagram_service.create(name="a\x00b")
