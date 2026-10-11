"""The MCP adapter's tools, called directly against a doubled graph.

This is the analogue of `tests/e2e/test_architecture_api.py` for the other
adapter: what a tool answers, and what it refuses. The tools are called through
`MCPServer.call_tool`, which is the same entry point the transport uses, so a
tool that is registered wrongly fails here rather than at the first agent that
tries it. The HTTP side is covered in `tests/e2e/test_mcp_endpoint.py`.
"""

from __future__ import annotations

import base64
import re
from typing import Any
from uuid import UUID, uuid4

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ea.api.schemas import (
    AddressAssign,
    ApprovalRequest,
    AxisSelect,
    BaselineCreate,
    ElementCreate,
    ElementQuerySchema,
    ElementUpdate,
    FileDetailsWrite,
    MoveElements,
    PackageCreate,
    PackageUpdate,
    RelationshipCreate,
    RestoreRequest,
    SavedQueryCreate,
    SubnetCreate,
)
from ea.core.config import Settings
from ea.main import create_app
from ea.mcp import build_mcp_server
from ea.mcp.server import INSTRUCTIONS
from ea.services.analysis import AnalysisService
from ea.services.architecture import ArchitectureService
from ea.services.audit import AuditService
from ea.services.baselines import BaselineService
from ea.services.caller import acting_as
from ea.services.diagrams import DiagramService
from ea.services.documents import DocumentService
from ea.services.exchanges import ExchangeService
from ea.services.files import FileService
from ea.services.ipam import IpamService
from ea.services.packages import PackageService
from ea.services.saved_queries import SavedQueryService
from tests.conftest import (
    InMemoryAuditLog,
    InMemoryDiagrams,
    InMemoryExchange,
    InMemoryObjectStore,
    InMemoryRepository,
    a_reader,
    an_approver,
)
from tests.exchange_files import DATA


@pytest.fixture
def server(
    service: ArchitectureService,
    document_service: DocumentService,
    ipam: IpamService,
    file_service: FileService,
    package_service: PackageService,
    saved_query_service: SavedQueryService,
    audit_log: InMemoryAuditLog,
    baseline_service: BaselineService,
    exchange_service: ExchangeService,
    analysis: AnalysisService,
) -> MCPServer[Any]:
    """The adapter over the in-memory stores — no database, no transport."""
    return build_mcp_server(
        lambda: service,
        lambda: document_service,
        lambda: ipam,
        lambda: file_service,
        lambda: package_service,
        lambda: saved_query_service,
        lambda: AuditService(audit_log),
        lambda: baseline_service,
        lambda: exchange_service,
        lambda: analysis,
    )


@pytest.fixture
def server_without_an_index(
    service: ArchitectureService,
    document_service_without_an_index: DocumentService,
    ipam: IpamService,
    file_service: FileService,
    package_service: PackageService,
    saved_query_service: SavedQueryService,
    audit_log: InMemoryAuditLog,
    baseline_service: BaselineService,
    exchange_service: ExchangeService,
    analysis: AnalysisService,
) -> MCPServer[Any]:
    """The same adapter on a deployment with `EA_EMBEDDINGS_ENABLED` off."""
    return build_mcp_server(
        lambda: service,
        lambda: document_service_without_an_index,
        lambda: ipam,
        lambda: file_service,
        lambda: package_service,
        lambda: saved_query_service,
        lambda: AuditService(audit_log),
        lambda: baseline_service,
        lambda: exchange_service,
        lambda: analysis,
    )


async def call(server: MCPServer[Any], tool: str, **arguments: Any) -> Any:
    """Call a tool and hand back the structured payload an agent would read."""
    result = await server.call_tool(tool, arguments)
    assert not result.is_error, result.content
    return result.structured_content


async def an_element(server: MCPServer[Any], element_type: str, name: str) -> Any:
    return await call(server, "create_element", element_type=element_type, name=name)


@pytest.mark.asyncio
class TestTheAuditTools:
    async def test_an_agent_reads_the_history_of_an_element_it_changed(
        self, server: MCPServer[Any]
    ) -> None:
        created = await an_element(server, "node", "db-01")
        await call(server, "update_element", element_id=created["id"], name="db-02")

        history = await call(server, "get_element_history", element_id=created["id"])
        everything = await call(server, "list_audit_events", action="element.created")

        assert {i["action"] for i in history["items"]} == {"element.updated", "element.created"}
        assert {i["channel"] for i in history["items"]} == {"mcp"}
        assert [i["entity_id"] for i in everything["items"]] == [created["id"]]

    async def test_a_reader_may_read_the_trail(self, server: MCPServer[Any]) -> None:
        await an_element(server, "node", "db-01")

        with acting_as(a_reader()):
            page = await call(server, "list_audit_events")

        assert len(page["items"]) == 1

    async def test_an_anonymous_agent_is_refused(
        self, server: MCPServer[Any], nobody_calling: None
    ) -> None:
        with pytest.raises(ToolError):
            await server.call_tool("list_audit_events", {})

    async def test_a_forged_cursor_is_told_to_the_agent_not_hidden(
        self, server: MCPServer[Any]
    ) -> None:
        with pytest.raises(ToolError, match="cursor"):
            await server.call_tool("list_audit_events", {"cursor": "garbage!"})

    async def test_both_tools_only_read(self, server: MCPServer[Any]) -> None:
        tools = {tool.name: tool for tool in await server.list_tools()}

        for name in ("list_audit_events", "get_element_history"):
            annotations = tools[name].annotations
            assert annotations is not None
            assert annotations.read_only_hint is True, name

    async def test_the_instructions_point_an_agent_at_the_history(self) -> None:
        assert "get_element_history" in INSTRUCTIONS
        assert "list_audit_events" in INSTRUCTIONS


@pytest.mark.asyncio
class TestTheBaselineTools:
    async def test_restoring_is_a_dry_run_until_the_agent_says_otherwise(
        self, server: MCPServer[Any]
    ) -> None:
        gone = await an_element(server, "node", "gone")
        baseline = await call(server, "create_baseline", name="v1")
        await call(server, "delete_element", element_id=gone["id"])

        planned = await call(server, "restore_baseline", baseline_id=baseline["id"])
        with pytest.raises(ToolError):
            await server.call_tool("get_element", {"element_id": gone["id"]})
        applied = await call(
            server,
            "restore_baseline",
            baseline_id=baseline["id"],
            dry_run=False,
            expected_digest=planned["digest"],
        )
        back = await call(server, "get_element", element_id=gone["id"])

        assert planned["applied"] is False and planned["plan"]["elements_to_create"] == 1
        assert applied["applied"] is True
        assert back["name"] == "gone"

    async def test_applying_without_the_plan_that_was_read_writes_nothing(
        self, server: MCPServer[Any]
    ) -> None:
        gone = await an_element(server, "node", "gone")
        baseline = await call(server, "create_baseline", name="v1")
        await call(server, "delete_element", element_id=gone["id"])
        planned = await call(server, "restore_baseline", baseline_id=baseline["id"])

        with pytest.raises(ToolError, match="expected_digest"):
            await server.call_tool(
                "restore_baseline", {"baseline_id": baseline["id"], "dry_run": False}
            )
        await an_element(server, "node", "late")
        with pytest.raises(ToolError, match="read the plan again"):
            await server.call_tool(
                "restore_baseline",
                {
                    "baseline_id": baseline["id"],
                    "dry_run": False,
                    "expected_digest": planned["digest"],
                },
            )
        with pytest.raises(ToolError):
            await server.call_tool("get_element", {"element_id": gone["id"]})

    async def test_the_destructive_tool_says_so_to_the_client(self, server: MCPServer[Any]) -> None:
        tools = {t.name: t for t in await server.list_tools()}

        restore = tools["restore_baseline"]
        assert restore.annotations is not None and restore.annotations.destructive_hint is True
        assert restore.annotations.read_only_hint is False
        assert "dry_run" in (restore.description or "")
        for name in ("list_baselines", "diff_baselines"):
            assert tools[name].annotations is not None
            assert tools[name].annotations.read_only_hint is True  # type: ignore[union-attr]
        create = tools["create_baseline"].annotations
        assert create is not None and create.read_only_hint is False
        assert create.destructive_hint is False

    async def test_listing_and_diffing(self, server: MCPServer[Any]) -> None:
        baseline = await call(server, "create_baseline", name="v1")
        await an_element(server, "node", "new")

        listed = await call(server, "list_baselines")
        diff = await call(server, "diff_baselines", baseline_id=baseline["id"])

        assert [b["name"] for b in listed["result"]] == ["v1"]
        assert [e["label"] for e in diff["elements_added"]] == ["new"]

    async def test_a_reader_may_read_and_not_write(self, server: MCPServer[Any]) -> None:
        baseline = await call(server, "create_baseline", name="v1")

        with acting_as(a_reader()):
            assert (await server.call_tool("list_baselines", {})).is_error is False
            with pytest.raises(ToolError):
                await server.call_tool("create_baseline", {"name": "x"})
            with pytest.raises(ToolError):
                await server.call_tool("restore_baseline", {"baseline_id": baseline["id"]})

    async def test_the_instructions_make_a_dry_run_the_first_step(self) -> None:
        for phrase in ("create_baseline", "diff_baselines", "dry_run", "expected_digest"):
            assert phrase in INSTRUCTIONS, phrase


@pytest.mark.asyncio
class TestTheReviewTool:
    async def test_the_roles_of_the_token_decide(self, server: MCPServer[Any]) -> None:
        element = await an_element(server, "node", "db-01")
        submitted = await call(
            server, "transition_element", element_id=element["id"], transition="submit"
        )

        with pytest.raises(ToolError, match="ea-approver"):
            await server.call_tool(
                "transition_element",
                {"element_id": element["id"], "transition": "approve", "expected_revision": 1},
            )
        with acting_as(an_approver()):
            approved = await call(
                server,
                "transition_element",
                element_id=element["id"],
                transition="approve",
                expected_revision=submitted["revision"],
            )

        assert submitted["approval_state"] == "in_review"
        assert approved["approval_state"] == "approved"
        assert approved["allowed_transitions"] == ["reopen", "lock"]

    async def test_approving_a_revision_the_agent_did_not_read_is_told_to_it(
        self, server: MCPServer[Any]
    ) -> None:
        element = await an_element(server, "node", "db-01")
        await call(server, "transition_element", element_id=element["id"], transition="submit")
        await call(server, "update_element", element_id=element["id"], description="later")

        with acting_as(an_approver()):
            with pytest.raises(ToolError, match="read it again"):
                await server.call_tool(
                    "transition_element",
                    {"element_id": element["id"], "transition": "approve", "expected_revision": 1},
                )
            with pytest.raises(ToolError, match="read it again"):
                await server.call_tool(
                    "transition_element", {"element_id": element["id"], "transition": "approve"}
                )

    async def test_an_approved_element_tells_the_agent_why_an_update_is_refused(
        self, server: MCPServer[Any]
    ) -> None:
        element = await an_element(server, "node", "db-01")
        await call(server, "transition_element", element_id=element["id"], transition="submit")
        with acting_as(an_approver()):
            await call(
                server,
                "transition_element",
                element_id=element["id"],
                transition="approve",
                expected_revision=element["revision"],
            )

        with pytest.raises(ToolError, match="approved"):
            await server.call_tool("update_element", {"element_id": element["id"], "name": "x"})
        with pytest.raises(ToolError, match="approved"):
            await server.call_tool("delete_element", {"element_id": element["id"]})

    async def test_it_writes_without_destroying_and_the_instructions_explain_it(
        self, server: MCPServer[Any]
    ) -> None:
        tool = next(t for t in await server.list_tools() if t.name == "transition_element")

        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.destructive_hint is False
        for phrase in ("transition_element", "expected_revision", "allowed_transitions"):
            assert phrase in INSTRUCTIONS, phrase


@pytest.mark.asyncio
class TestTheToolset:
    async def test_it_exposes_the_whole_service_and_nothing_else(
        self, server: MCPServer[Any]
    ) -> None:
        """The list is asserted whole: a tool added without a test is a failure."""
        names = {tool.name for tool in await server.list_tools()}

        assert (
            len(names) == 55
        )  # fifty-one, the two exchange tools, the matrix and the quality check
        assert names == {
            "export_model",
            "import_model",
            "create_element",
            "get_element",
            "list_elements",
            "update_element",
            "delete_element",
            "connect_elements",
            "list_relationships",
            "read_element_relations",
            "disconnect_elements",
            "neighbourhood",
            "impact_of",
            "describe_metamodel",
            "permitted_relationships",
            "relationship_matrix_row",
            "get_relationship_matrix",
            "check_model",
            "attach_document",
            "list_documents",
            "read_document",
            "revise_document",
            "discard_document",
            "search_documents",
            "declare_ip_subnet",
            "list_ip_subnets",
            "read_ip_subnet",
            "allocate_ip_address",
            "assign_ip_address",
            "locate_ip_address",
            "list_ip_addresses",
            "release_ip_address",
            "list_files",
            "read_file",
            "describe_file",
            "set_file_details",
            "upload_file",
            "delete_file",
            "list_packages",
            "create_package",
            "update_package",
            "delete_package",
            "move_elements",
            "search_elements",
            "list_saved_queries",
            "save_query",
            "run_saved_query",
            "delete_saved_query",
            "list_audit_events",
            "get_element_history",
            "list_baselines",
            "create_baseline",
            "diff_baselines",
            "restore_baseline",
            "transition_element",
        }

    async def test_every_tool_says_whether_it_writes(self, server: MCPServer[Any]) -> None:
        """An agent decides whether to ask first from the hint, so it must be set."""
        for tool in await server.list_tools():
            assert tool.annotations is not None, tool.name
            assert tool.annotations.read_only_hint is not None, tool.name

    async def test_the_three_deletions_are_flagged_destructive(
        self, server: MCPServer[Any]
    ) -> None:
        annotations = {
            tool.name: tool.annotations
            for tool in await server.list_tools()
            if tool.annotations is not None
        }

        for name in ("delete_element", "disconnect_elements", "discard_document"):
            assert annotations[name].read_only_hint is False, name
            assert annotations[name].destructive_hint is True, name

    async def test_the_two_new_deletions_are_flagged_destructive_and_filing_is_not(
        self, server: MCPServer[Any]
    ) -> None:
        annotations = {
            tool.name: tool.annotations
            for tool in await server.list_tools()
            if tool.annotations is not None
        }

        for name in ("delete_package", "delete_saved_query"):
            assert annotations[name].destructive_hint is True, name
        for name in ("move_elements", "update_package", "create_package", "save_query"):
            assert annotations[name].read_only_hint is False, name
            assert annotations[name].destructive_hint is False, name
        for name in ("list_packages", "search_elements", "list_saved_queries", "run_saved_query"):
            assert annotations[name].read_only_hint is True, name

    async def test_the_instructions_teach_packages_and_the_structured_search(self) -> None:
        for phrase in (
            "list_packages",
            "move_elements",
            "root",
            "search_elements",
            "list_saved_queries",
            "run_saved_query",
            "save_query",
        ):
            assert phrase in INSTRUCTIONS, phrase

    async def test_the_filing_tools_tell_the_model_they_delete_no_element(
        self, server: MCPServer[Any]
    ) -> None:
        tools = {tool.name: tool for tool in await server.list_tools()}

        assert "never" in (tools["delete_package"].description or "").lower()
        assert "no fact" in (tools["move_elements"].description or "").lower()

    async def test_every_tool_carries_a_description_for_the_model(
        self, server: MCPServer[Any]
    ) -> None:
        for tool in await server.list_tools():
            assert tool.description, tool.name

    async def test_the_element_type_cannot_be_updated(self, server: MCPServer[Any]) -> None:
        """Changing a type could invalidate stored links; it is a migration, not an edit."""
        update = next(tool for tool in await server.list_tools() if tool.name == "update_element")

        assert "element_type" not in update.input_schema["properties"]

    async def test_the_type_of_a_new_element_is_a_closed_list(self, server: MCPServer[Any]) -> None:
        """The 61 types reach the agent as a schema enum, so it never invents one."""
        create = next(tool for tool in await server.list_tools() if tool.name == "create_element")
        schema = create.input_schema

        rendered = str(schema)
        assert "application_component" in rendered
        assert "microservice" not in rendered


@pytest.mark.asyncio
class TestElements:
    async def test_creating_an_element_resolves_its_layer_and_aspect(
        self, server: MCPServer[Any]
    ) -> None:
        element = await call(
            server,
            "create_element",
            element_type="application_component",
            name="Billing",
            properties={"owner": "finance"},
        )

        assert element["layer"] == "application"
        assert element["aspect"] == "active_structure"
        assert element["properties"] == {"owner": "finance"}

    async def test_an_element_can_be_read_back_by_id(self, server: MCPServer[Any]) -> None:
        created = await an_element(server, "node", "db-01")

        assert (await call(server, "get_element", element_id=created["id"]))["name"] == "db-01"

    async def test_an_unknown_type_is_refused_before_anything_is_stored(
        self, server: MCPServer[Any]
    ) -> None:
        with pytest.raises(ToolError):
            await call(server, "create_element", element_type="microservice", name="Billing")

        assert (await call(server, "list_elements"))["total"] == 0

    async def test_a_blank_name_is_refused(self, server: MCPServer[Any]) -> None:
        with pytest.raises(ToolError):
            await call(server, "create_element", element_type="node", name="   ")

    async def test_a_missing_element_is_named_in_a_message_the_model_can_act_on(
        self, server: MCPServer[Any]
    ) -> None:
        """A `ToolError` is the anticipated failure: the agent reads it and retries."""
        missing = uuid4()

        with pytest.raises(ToolError) as failure:
            await call(server, "get_element", element_id=str(missing))

        assert str(missing) in str(failure.value)

    async def test_the_catalogue_reports_the_total_beside_the_page(
        self, server: MCPServer[Any]
    ) -> None:
        for index in range(3):
            await an_element(server, "node", f"node-{index}")

        page = await call(server, "list_elements", limit=2)

        assert page["total"] == 3
        assert len(page["items"]) == 2

    async def test_an_update_leaves_the_fields_it_omits_alone(self, server: MCPServer[Any]) -> None:
        created = await call(
            server,
            "create_element",
            element_type="application_component",
            name="Billing",
            description="invoices",
        )

        updated = await call(server, "update_element", element_id=created["id"], name="Invoicing")

        assert updated["name"] == "Invoicing"
        assert updated["description"] == "invoices"
        assert updated["element_type"] == "application_component"

    async def test_deleting_an_element_removes_it_from_the_catalogue(
        self, server: MCPServer[Any]
    ) -> None:
        created = await an_element(server, "node", "db-01")

        await call(server, "delete_element", element_id=created["id"])

        assert (await call(server, "list_elements"))["total"] == 0

    async def test_deleting_twice_is_reported_rather_than_ignored(
        self, server: MCPServer[Any]
    ) -> None:
        created = await an_element(server, "node", "db-01")
        await call(server, "delete_element", element_id=created["id"])

        with pytest.raises(ToolError):
            await call(server, "delete_element", element_id=created["id"])


@pytest.mark.asyncio
class TestRelationships:
    async def test_two_elements_can_be_linked_when_archimate_allows_it(
        self, server: MCPServer[Any]
    ) -> None:
        source = await an_element(server, "application_component", "Billing")
        target = await an_element(server, "application_service", "Invoice API")

        link = await call(
            server,
            "connect_elements",
            relationship_type="realization",
            source_id=source["id"],
            target_id=target["id"],
        )

        assert link["source_type"] == "application_component"
        assert link["target_type"] == "application_service"

    async def test_a_link_the_metamodel_forbids_is_refused_with_its_reason(
        self, server: MCPServer[Any]
    ) -> None:
        """ArchiMate joins a service to a node by `serving`, never by owning it."""
        source = await an_element(server, "application_service", "Invoice API")
        target = await an_element(server, "node", "db-01")

        with pytest.raises(ToolError) as failure:
            await call(
                server,
                "connect_elements",
                relationship_type="composition",
                source_id=source["id"],
                target_id=target["id"],
            )

        assert "composition" in str(failure.value).lower()

    async def test_linking_to_an_element_that_does_not_exist_is_refused(
        self, server: MCPServer[Any]
    ) -> None:
        source = await an_element(server, "application_component", "Billing")

        with pytest.raises(ToolError):
            await call(
                server,
                "connect_elements",
                relationship_type="serving",
                source_id=source["id"],
                target_id=str(uuid4()),
            )

    async def test_the_links_of_an_element_come_back_with_both_endpoints(
        self, server: MCPServer[Any]
    ) -> None:
        """A link stores ids and types, never names — the sub-graph carries those."""
        source = await an_element(server, "application_component", "Billing")
        target = await an_element(server, "application_service", "Invoice API")
        await call(
            server,
            "connect_elements",
            relationship_type="realization",
            source_id=source["id"],
            target_id=target["id"],
        )

        view = await call(server, "read_element_relations", element_id=source["id"])

        assert {element["name"] for element in view["elements"]} == {"Billing", "Invoice API"}
        assert len(view["relationships"]) == 1

    async def test_a_link_can_be_removed(self, server: MCPServer[Any]) -> None:
        source = await an_element(server, "application_component", "Billing")
        target = await an_element(server, "application_service", "Invoice API")
        link = await call(
            server,
            "connect_elements",
            relationship_type="realization",
            source_id=source["id"],
            target_id=target["id"],
        )

        await call(server, "disconnect_elements", relationship_id=link["id"])

        assert await call(server, "list_relationships") == {"result": []}


@pytest.mark.asyncio
class TestTraversals:
    async def test_the_neighbourhood_of_an_element_is_a_sub_graph(
        self, server: MCPServer[Any]
    ) -> None:
        created = await an_element(server, "application_component", "Billing")

        view = await call(server, "neighbourhood", element_id=created["id"], depth=2)

        assert [element["name"] for element in view["elements"]] == ["Billing"]

    async def test_the_impact_of_an_element_is_a_sub_graph(self, server: MCPServer[Any]) -> None:
        created = await an_element(server, "application_component", "Billing")

        view = await call(server, "impact_of", element_id=created["id"])

        assert [element["name"] for element in view["elements"]] == ["Billing"]

    async def test_a_depth_beyond_the_traversal_bound_is_refused(
        self, server: MCPServer[Any]
    ) -> None:
        """The bound is the one the query is written against, not a suggestion."""
        created = await an_element(server, "node", "db-01")

        with pytest.raises(ToolError):
            await call(server, "neighbourhood", element_id=created["id"], depth=99)


@pytest.mark.asyncio
class TestMetamodel:
    async def test_the_palette_is_served_whole(self, server: MCPServer[Any]) -> None:
        """61 element types and 11 relationships — the agent asks, it never guesses."""
        metamodel = await call(server, "describe_metamodel")

        assert len(metamodel["element_types"]) == 61
        assert len(metamodel["relationship_types"]) == 11

    async def test_a_relationship_type_says_which_way_a_failure_travels(
        self, server: MCPServer[Any]
    ) -> None:
        metamodel = await call(server, "describe_metamodel")
        by_value = {entry["value"]: entry for entry in metamodel["relationship_types"]}

        assert by_value["serving"]["impact_follows_direction"] is True
        assert by_value["composition"]["impact_follows_direction"] is False

    async def test_what_may_run_between_two_types_is_answered_from_the_rules(
        self, server: MCPServer[Any]
    ) -> None:
        permitted = await call(
            server,
            "permitted_relationships",
            source="application_component",
            target="application_service",
        )

        assert "realization" in permitted["result"]
        assert "composition" not in permitted["result"]

    async def test_one_row_of_the_matrix_is_a_lookup_not_a_dump(
        self, server: MCPServer[Any]
    ) -> None:
        row = await call(server, "relationship_matrix_row", source="application_component")

        assert row["source"] == "application_component"
        assert len(row["rules"]) == 61


@pytest.mark.asyncio
class TestDocuments:
    """The markdown attached to an element, offered to an agent as text.

    The HTTP adapter takes an upload; there is no file here, so what is under
    test is that the same rules still hold when the content arrives as a
    string — the element must exist, the name must be markdown, and a listing
    still refuses to carry the bodies.
    """

    async def test_a_document_is_attached_and_read_back_whole(self, server: MCPServer[Any]) -> None:
        element = await an_element(server, "application_component", "Billing")

        attached = await call(
            server,
            "attach_document",
            element_id=element["id"],
            filename="runbook.md",
            content="# Runbook\n\nRestart the service.\n",
        )

        read = await call(server, "read_document", document_id=attached["id"])
        assert read["content"] == "# Runbook\n\nRestart the service.\n"
        assert read["element_id"] == element["id"]

    async def test_a_listing_names_the_files_without_carrying_their_text(
        self, server: MCPServer[Any]
    ) -> None:
        """The whole point of the two read models: ten names cost ten names."""
        element = await an_element(server, "application_component", "Billing")
        await call(
            server,
            "attach_document",
            element_id=element["id"],
            filename="runbook.md",
            content="# Runbook\n",
        )

        listed = await call(server, "list_documents", element_id=element["id"])

        assert [summary["filename"] for summary in listed["result"]] == ["runbook.md"]
        assert "content" not in listed["result"][0]
        assert listed["result"][0]["byte_size"] == len("# Runbook\n")

    async def test_an_element_with_nothing_attached_answers_an_empty_list(
        self, server: MCPServer[Any]
    ) -> None:
        element = await an_element(server, "node", "db-01")

        assert await call(server, "list_documents", element_id=element["id"]) == {"result": []}

    async def test_listing_an_element_that_does_not_exist_is_an_error_not_an_empty_list(
        self, server: MCPServer[Any]
    ) -> None:
        """ "No such element" and "no documents" are different answers."""
        with pytest.raises(ToolError):
            await call(server, "list_documents", element_id=str(uuid4()))

    async def test_attaching_to_an_element_that_does_not_exist_is_refused(
        self, server: MCPServer[Any]
    ) -> None:
        """No foreign key states this — the service does, and the adapter cannot skip it."""
        with pytest.raises(ToolError):
            await call(
                server,
                "attach_document",
                element_id=str(uuid4()),
                filename="runbook.md",
                content="# Runbook\n",
            )

    async def test_a_name_that_is_not_markdown_is_refused_with_its_reason(
        self, server: MCPServer[Any]
    ) -> None:
        element = await an_element(server, "application_component", "Billing")

        with pytest.raises(ToolError) as failure:
            await call(
                server,
                "attach_document",
                element_id=element["id"],
                filename="notes.txt",
                content="plain\n",
            )

        assert ".md" in str(failure.value)

    async def test_the_same_name_twice_on_one_element_is_refused(
        self, server: MCPServer[Any]
    ) -> None:
        element = await an_element(server, "application_component", "Billing")
        await call(
            server,
            "attach_document",
            element_id=element["id"],
            filename="runbook.md",
            content="# Runbook\n",
        )

        with pytest.raises(ToolError):
            await call(
                server,
                "attach_document",
                element_id=element["id"],
                filename="runbook.md",
                content="# Other\n",
            )

    async def test_a_revision_replaces_the_text_under_the_same_id(
        self, server: MCPServer[Any]
    ) -> None:
        element = await an_element(server, "application_component", "Billing")
        stored = await call(
            server,
            "attach_document",
            element_id=element["id"],
            filename="runbook.md",
            content="# Runbook\n",
        )

        revised = await call(
            server,
            "revise_document",
            document_id=stored["id"],
            filename="runbook.md",
            content="# Runbook v2\n",
        )

        assert revised["id"] == stored["id"]
        assert (await call(server, "read_document", document_id=stored["id"]))["content"] == (
            "# Runbook v2\n"
        )

    async def test_revising_under_a_different_name_is_refused(self, server: MCPServer[Any]) -> None:
        """A reader knows a document by its name, so the name must keep its text."""
        element = await an_element(server, "application_component", "Billing")
        stored = await call(
            server,
            "attach_document",
            element_id=element["id"],
            filename="runbook.md",
            content="# Runbook\n",
        )

        with pytest.raises(ToolError) as failure:
            await call(
                server,
                "revise_document",
                document_id=stored["id"],
                filename="notes.md",
                content="# Something else\n",
            )

        assert "runbook.md" in str(failure.value)

    async def test_a_document_can_be_discarded_on_its_own(self, server: MCPServer[Any]) -> None:
        element = await an_element(server, "application_component", "Billing")
        stored = await call(
            server,
            "attach_document",
            element_id=element["id"],
            filename="runbook.md",
            content="# Runbook\n",
        )

        await call(server, "discard_document", document_id=stored["id"])

        assert await call(server, "list_documents", element_id=element["id"]) == {"result": []}

    async def test_discarding_twice_is_reported_rather_than_ignored(
        self, server: MCPServer[Any]
    ) -> None:
        element = await an_element(server, "application_component", "Billing")
        stored = await call(
            server,
            "attach_document",
            element_id=element["id"],
            filename="runbook.md",
            content="# Runbook\n",
        )
        await call(server, "discard_document", document_id=stored["id"])

        with pytest.raises(ToolError):
            await call(server, "discard_document", document_id=stored["id"])


@pytest.mark.asyncio
class TestSearchingTheDocuments:
    """The tool an agent reaches for when it does not know which file to read."""

    async def test_it_answers_with_passages_rather_than_file_names(
        self, server: MCPServer[Any]
    ) -> None:
        element = await an_element(server, "application_component", "Facturation")
        await call(
            server,
            "attach_document",
            element_id=element["id"],
            filename="runbook.md",
            content="# Runbook\n\nQuoi faire.\n\n## Escalade\n\nAppeler Nicolas.\n",
        )

        hits = await call(server, "search_documents", question="Escalade")

        assert hits["result"][0]["trail"] == "runbook.md > Runbook > Escalade"
        assert hits["result"][0]["text"] == "Appeler Nicolas."
        assert hits["result"][0]["element_id"] == element["id"]

    async def test_it_can_be_scoped_to_one_element(self, server: MCPServer[Any]) -> None:
        first = await an_element(server, "application_component", "Facturation")
        second = await an_element(server, "application_component", "Commandes")
        for element in (first, second):
            await call(
                server,
                "attach_document",
                element_id=element["id"],
                filename="runbook.md",
                content="# Runbook\n\n## Escalade\n\nAppeler Nicolas.\n",
            )

        hits = await call(server, "search_documents", question="Escalade", element_id=second["id"])

        assert {hit["element_id"] for hit in hits["result"]} == {second["id"]}

    async def test_it_reads_the_index_and_never_writes(self, server: MCPServer[Any]) -> None:
        """A client shows this hint to whoever has to approve the call."""
        tool = next(t for t in await server.list_tools() if t.name == "search_documents")

        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True

    async def test_a_deployment_with_no_index_says_so_in_a_sentence(
        self, server_without_an_index: MCPServer[Any]
    ) -> None:
        """Not "error executing tool": the agent can read this and stop trying."""
        with pytest.raises(ToolError, match="not enabled"):
            await call(server_without_an_index, "search_documents", question="quoi que ce soit")

    async def test_a_blank_question_is_refused_with_a_reason(self, server: MCPServer[Any]) -> None:
        with pytest.raises(ToolError, match="needs a question"):
            await call(server, "search_documents", question="   ")


@pytest.mark.asyncio
class TestTheIpAddressing:
    """The tools an agent maintains the IP inventory with (docs/adr/0020).

    They are the reason the addressing is not left to `update_element`: the
    arithmetic — what is free, what is taken, what a prefix keeps for itself —
    is the server's job, and an agent that had to do it in its head would
    eventually hand out an address twice.
    """

    async def test_a_subnet_is_declared_as_an_ordinary_element(
        self, server: MCPServer[Any]
    ) -> None:
        subnet = await call(server, "declare_ip_subnet", name="DMZ", cidr="10.0.1.0/24")

        assert subnet["cidr"] == "10.0.1.0/24"
        element = await call(server, "get_element", element_id=subnet["element_id"])
        assert element["element_type"] == "communication_network"

    async def test_a_prefix_written_with_host_bits_is_refused_with_the_one_meant(
        self, server: MCPServer[Any]
    ) -> None:
        with pytest.raises(ToolError, match=re.escape("10.0.1.0/24")):
            await call(server, "declare_ip_subnet", name="DMZ", cidr="10.0.1.5/24")

    async def test_allocating_hands_out_the_first_free_address(
        self, server: MCPServer[Any]
    ) -> None:
        subnet = await call(
            server, "declare_ip_subnet", name="DMZ", cidr="10.0.1.0/24", reserved="10.0.1.1"
        )
        host = await an_element(server, "node", "srv-app-01")

        assigned = await call(
            server,
            "allocate_ip_address",
            subnet_id=subnet["element_id"],
            element_id=host["id"],
        )

        assert assigned["address"] == "10.0.1.2"
        assert assigned["element_name"] == "srv-app-01"

    async def test_an_address_another_element_holds_is_refused_by_name(
        self, server: MCPServer[Any]
    ) -> None:
        """The refusal names the holder, so the agent can say what is in the way."""
        await call(server, "declare_ip_subnet", name="DMZ", cidr="10.0.1.0/24")
        first = await an_element(server, "node", "srv-app-01")
        second = await an_element(server, "node", "srv-app-02")
        await call(server, "assign_ip_address", element_id=first["id"], address="10.0.1.12")

        with pytest.raises(ToolError, match="srv-app-01"):
            await call(server, "assign_ip_address", element_id=second["id"], address="10.0.1.12")

    async def test_something_that_cannot_be_pinged_is_refused(self, server: MCPServer[Any]) -> None:
        await call(server, "declare_ip_subnet", name="DMZ", cidr="10.0.1.0/24")
        process = await an_element(server, "business_process", "Facturer")

        with pytest.raises(ToolError, match="business_process"):
            await call(server, "assign_ip_address", element_id=process["id"], address="10.0.1.12")

    async def test_locating_an_address_answers_with_the_element_and_its_links(
        self, server: MCPServer[Any]
    ) -> None:
        await call(server, "declare_ip_subnet", name="DMZ", cidr="10.0.1.0/24")
        host = await an_element(server, "node", "srv-app-01")
        billing = await an_element(server, "application_component", "Billing")
        await call(server, "assign_ip_address", element_id=host["id"], address="10.0.1.12")
        await call(
            server,
            "connect_elements",
            relationship_type="serving",
            source_id=host["id"],
            target_id=billing["id"],
        )

        found = await call(server, "locate_ip_address", address="10.0.1.12")

        assert found["address"]["element_name"] == "srv-app-01"
        assert {element["name"] for element in found["graph"]["elements"]} == {
            "srv-app-01",
            "Billing",
        }

    async def test_the_inventory_can_be_narrowed_to_a_prefix_nobody_declared(
        self, server: MCPServer[Any]
    ) -> None:
        subnet = await call(server, "declare_ip_subnet", name="DMZ", cidr="10.0.1.0/24")
        for name in ("srv-app-01", "srv-app-02"):
            host = await an_element(server, "node", name)
            await call(
                server,
                "allocate_ip_address",
                subnet_id=subnet["element_id"],
                element_id=host["id"],
            )

        listed = await call(server, "list_ip_addresses", within="10.0.1.0/31")

        assert [entry["address"] for entry in listed["result"]] == ["10.0.1.1"]

    async def test_releasing_frees_the_address_for_the_next_allocation(
        self, server: MCPServer[Any]
    ) -> None:
        subnet = await call(server, "declare_ip_subnet", name="DMZ", cidr="10.0.1.0/24")
        first = await an_element(server, "node", "srv-app-01")
        await call(
            server, "allocate_ip_address", subnet_id=subnet["element_id"], element_id=first["id"]
        )

        await call(server, "release_ip_address", element_id=first["id"])

        second = await an_element(server, "node", "srv-app-02")
        reused = await call(
            server, "allocate_ip_address", subnet_id=subnet["element_id"], element_id=second["id"]
        )
        assert reused["address"] == "10.0.1.1"

    async def test_a_subnet_says_how_full_it_is_and_what_comes_next(
        self, server: MCPServer[Any]
    ) -> None:
        subnet = await call(server, "declare_ip_subnet", name="Lien", cidr="10.0.1.0/30")
        host = await an_element(server, "node", "srv-app-01")
        await call(
            server, "allocate_ip_address", subnet_id=subnet["element_id"], element_id=host["id"]
        )

        detail = await call(server, "read_ip_subnet", element_id=subnet["element_id"])

        assert (detail["subnet"]["used"], detail["subnet"]["free"]) == (1, 1)
        assert detail["next_free"] == "10.0.1.2"

    async def test_a_full_subnet_refuses_instead_of_inventing_an_address(
        self, server: MCPServer[Any]
    ) -> None:
        subnet = await call(server, "declare_ip_subnet", name="Lien", cidr="10.0.1.0/30")
        for name in ("a", "b"):
            host = await an_element(server, "node", name)
            await call(
                server,
                "allocate_ip_address",
                subnet_id=subnet["element_id"],
                element_id=host["id"],
            )
        latecomer = await an_element(server, "node", "c")

        with pytest.raises(ToolError, match="no free address"):
            await call(
                server,
                "allocate_ip_address",
                subnet_id=subnet["element_id"],
                element_id=latecomer["id"],
            )


@pytest.mark.asyncio
class TestTheFileTools:
    """The bucket of any-kind files, offered to an agent (docs/adr/0036).

    Text arrives and leaves as text; anything else travels base64-encoded,
    since MCP arguments and results are JSON. The rules that need more than
    one call — no silent overwrite, no reading a binary as text — are
    `FileService`'s, exactly as `TestDocuments` above proves for the markdown.
    """

    async def test_text_goes_in_as_text_and_comes_back(
        self, server: MCPServer[Any], files_store: InMemoryObjectStore
    ) -> None:
        await call(server, "upload_file", key="inbox/notes.md", content="# Notes")

        assert files_store.objects["inbox/notes.md"] == (b"# Notes", "text/markdown")
        assert (await call(server, "read_file", key="inbox/notes.md"))["result"] == "# Notes"

    async def test_a_binary_goes_in_as_base64(
        self, server: MCPServer[Any], files_store: InMemoryObjectStore
    ) -> None:
        await call(server, "upload_file", key="a.png", content="iVBORw0K", encoding="base64")

        assert files_store.objects["a.png"] == (b"\x89PNG\r\n", "image/png")

    async def test_text_under_an_unknown_extension_is_plain_text(
        self, server: MCPServer[Any], files_store: InMemoryObjectStore
    ) -> None:
        await call(server, "upload_file", key="notes.zzz-unknown", content="x")

        assert files_store.objects["notes.zzz-unknown"][1] == "text/plain; charset=utf-8"

    async def test_broken_base64_is_explained_not_crashed(self, server: MCPServer[Any]) -> None:
        with pytest.raises(ToolError, match="base64"):
            await call(server, "upload_file", key="a.png", content="not base64!", encoding="base64")

    async def test_an_existing_file_is_not_replaced_unless_asked(
        self, server: MCPServer[Any]
    ) -> None:
        await call(server, "upload_file", key="a.md", content="one")

        with pytest.raises(ToolError, match="overwrite"):
            await call(server, "upload_file", key="a.md", content="two")

        await call(server, "upload_file", key="a.md", content="two", overwrite=True)

    async def test_a_binary_is_not_read_as_text(self, server: MCPServer[Any]) -> None:
        await call(server, "upload_file", key="a.bin", content="//79", encoding="base64")

        with pytest.raises(ToolError, match="UTF-8"):
            await call(server, "read_file", key="a.bin")

    async def test_a_folder_is_listed_one_level_deep(self, server: MCPServer[Any]) -> None:
        await call(server, "upload_file", key="inbox/sub/b.md", content="b")

        listing = await call(server, "list_files", prefix="inbox/")

        assert listing["folders"] == ["inbox/sub/"]

    async def test_delete_removes_and_is_announced_as_destructive(
        self, server: MCPServer[Any], files_store: InMemoryObjectStore
    ) -> None:
        await call(server, "upload_file", key="a.md", content="x")

        await call(server, "delete_file", key="a.md")

        assert files_store.objects == {}
        tool = next(t for t in await server.list_tools() if t.name == "delete_file")
        assert tool.annotations is not None
        assert tool.annotations.destructive_hint is True

    async def test_a_reader_cannot_upload(self, server: MCPServer[Any]) -> None:
        with acting_as(a_reader()), pytest.raises(ToolError):
            await call(server, "upload_file", key="a.md", content="x")

    async def test_a_decoded_payload_over_the_mcp_ceiling_is_refused(
        self, server: MCPServer[Any]
    ) -> None:
        """The SDK's request body and the deployed NGINX both cap far under 50 MB
        (Important 1 of the final review): the tool states its own, real limit
        rather than promise one the transport cannot carry."""
        from ea.mcp.server import MAX_MCP_UPLOAD_BYTES

        content = base64.b64encode(b"x" * (MAX_MCP_UPLOAD_BYTES + 1)).decode()

        with pytest.raises(ToolError, match="web interface"):
            await call(server, "upload_file", key="big.bin", content=content, encoding="base64")

    async def test_exactly_the_mcp_ceiling_is_accepted(
        self, server: MCPServer[Any], files_store: InMemoryObjectStore
    ) -> None:
        from ea.mcp.server import MAX_MCP_UPLOAD_BYTES

        content = base64.b64encode(b"x" * MAX_MCP_UPLOAD_BYTES).decode()

        await call(server, "upload_file", key="max.bin", content=content, encoding="base64")

        assert len(files_store.objects["max.bin"][0]) == MAX_MCP_UPLOAD_BYTES

    async def test_line_wrapped_base64_round_trips(
        self, server: MCPServer[Any], files_store: InMemoryObjectStore
    ) -> None:
        """What the `base64` CLI emits: wrapped every 76 characters."""
        raw = b"A" * 200
        encoded = base64.b64encode(raw).decode()
        wrapped = "\n".join(encoded[index : index + 76] for index in range(0, len(encoded), 76))

        await call(server, "upload_file", key="wrapped.bin", content=wrapped, encoding="base64")

        assert files_store.objects["wrapped.bin"][0] == raw


#: The JSON-schema keywords that *bound* a value. Descriptions are left out on
#: purpose: the model reads a tool's, a developer reads an endpoint's, and they
#: are allowed to say different things about the same field.
#: `maxItems` is here because a list argument carries its bound there and
#: nowhere else — `FileTags` caps how many tags a file may have.
BOUNDS = ("minLength", "maxLength", "minimum", "maximum", "pattern", "maxItems")


def bounds_of(schema: dict[str, Any]) -> dict[str, Any]:
    """The bounds of one property, looking inside `anyOf` for an optional one."""
    found = {key: schema[key] for key in BOUNDS if key in schema}
    for branch in schema.get("anyOf", ()):
        found.update(bounds_of(branch))
    return found


def query_parameter(openapi: dict[str, Any], path: str, name: str) -> dict[str, Any]:
    operation = openapi["paths"][path]["get"]
    return next(p["schema"] for p in operation["parameters"] if p["name"] == name)


@pytest.mark.asyncio
class TestTheRecordBesideAFile:
    """What an agent reads and writes about a file — see docs/adr/0039."""

    async def test_an_upload_records_a_title_a_description_and_tags(
        self, server: MCPServer[Any]
    ) -> None:
        await call(
            server,
            "upload_file",
            key="inbox/notes.md",
            content="# Notes",
            title="Les notes",
            description="Ce qui a été dit.",
            tags=["Réunion"],
        )

        described = await call(server, "describe_file", key="inbox/notes.md")

        recorded = described["metadata"]
        assert (recorded["title"], recorded["description"]) == ("Les notes", "Ce qui a été dit.")
        assert recorded["tags"] == ["réunion"]

    async def test_the_record_is_replaced_whole(self, server: MCPServer[Any]) -> None:
        """Which is why the tool says to read it first if only one field changes."""
        await call(server, "upload_file", key="a.md", content="x", title="A", tags=["budget"])

        await call(server, "set_file_details", key="a.md", title="B")

        recorded = (await call(server, "describe_file", key="a.md"))["metadata"]
        assert (recorded["title"], recorded["tags"]) == ("B", [])

    async def test_a_file_the_catalogue_never_saw_has_no_record(
        self, server: MCPServer[Any], files_store: InMemoryObjectStore
    ) -> None:
        files_store.objects["inbox/dropped.csv"] = (b"a,b\n", "text/csv")

        described = await call(server, "describe_file", key="inbox/dropped.csv")

        assert described["metadata"] is None

    async def test_describing_a_file_that_is_not_there_says_so(
        self, server: MCPServer[Any]
    ) -> None:
        with pytest.raises(ToolError):
            await call(server, "describe_file", key="nothing.md")


@pytest.mark.asyncio
class TestThePackageTools:
    """Packages are folders: a tool files things, and never deletes what it files."""

    async def test_the_tree_comes_back_flat_with_paths_and_direct_counts(
        self, server: MCPServer[Any]
    ) -> None:
        finance = await call(server, "create_package", name="Finance")
        await call(server, "create_package", name="Budget", parent_id=finance["id"])
        element = await an_element(server, "node", "db-01")
        await call(server, "move_elements", element_ids=[element["id"]], package_id=finance["id"])

        listed = (await call(server, "list_packages"))["result"]

        assert [(p["path"], p["element_count"], p["child_count"]) for p in listed] == [
            ("Finance", 1, 1),
            ("Finance / Budget", 0, 0),
        ]

    async def test_a_taken_sibling_name_is_told_to_the_model(self, server: MCPServer[Any]) -> None:
        await call(server, "create_package", name="Finance")

        with pytest.raises(ToolError, match="already named"):
            await call(server, "create_package", name="FINANCE")

    async def test_a_name_with_a_slash_is_refused_by_the_schema(
        self, server: MCPServer[Any]
    ) -> None:
        with pytest.raises(ToolError):
            await call(server, "create_package", name="a/b")

    async def test_the_parent_is_kept_when_omitted_and_the_word_root_is_the_top(
        self, server: MCPServer[Any]
    ) -> None:
        finance = await call(server, "create_package", name="Finance")
        budget = await call(server, "create_package", name="Budget", parent_id=finance["id"])

        kept = await call(server, "update_package", package_id=budget["id"], description="x")
        at_the_top = await call(server, "update_package", package_id=budget["id"], parent_id="root")
        back = await call(
            server, "update_package", package_id=budget["id"], parent_id=finance["id"]
        )

        assert kept["parent_id"] == finance["id"]
        assert at_the_top["parent_id"] is None
        assert back["parent_id"] == finance["id"]

    async def test_a_parent_that_is_neither_an_id_nor_root_is_refused(
        self, server: MCPServer[Any]
    ) -> None:
        package = await call(server, "create_package", name="Finance")

        with pytest.raises(ToolError, match="root"):
            await call(server, "update_package", package_id=package["id"], parent_id="ici")

    async def test_a_package_cannot_be_moved_below_itself(self, server: MCPServer[Any]) -> None:
        a = await call(server, "create_package", name="A")
        b = await call(server, "create_package", name="B", parent_id=a["id"])

        with pytest.raises(ToolError, match="inside itself"):
            await call(server, "update_package", package_id=a["id"], parent_id=b["id"])

    async def test_a_package_that_holds_something_is_not_deleted_until_told_where_it_goes(
        self, server: MCPServer[Any]
    ) -> None:
        package = await call(server, "create_package", name="A")
        element = await an_element(server, "node", "db-01")
        await call(server, "move_elements", element_ids=[element["id"]], package_id=package["id"])

        with pytest.raises(ToolError, match="move_contents_to"):
            await call(server, "delete_package", package_id=package["id"])

        await call(server, "delete_package", package_id=package["id"], move_contents_to="root")
        assert (await call(server, "get_element", element_id=element["id"]))["package_id"] is None
        assert (await call(server, "list_packages"))["result"] == []

    async def test_files_up_to_the_batch_at_once_and_back_to_the_root(
        self, server: MCPServer[Any]
    ) -> None:
        package = await call(server, "create_package", name="A")
        one, two = await an_element(server, "node", "one"), await an_element(server, "node", "two")

        await call(
            server, "move_elements", element_ids=[one["id"], two["id"]], package_id=package["id"]
        )
        assert (await call(server, "list_packages"))["result"][0]["element_count"] == 2

        await call(server, "move_elements", element_ids=[one["id"]], package_id="root")
        assert (await call(server, "list_packages"))["result"][0]["element_count"] == 1

    async def test_an_absent_element_moves_nothing(self, server: MCPServer[Any]) -> None:
        package = await call(server, "create_package", name="A")
        kept = await an_element(server, "node", "db-01")

        with pytest.raises(ToolError, match="no element"):
            await call(
                server,
                "move_elements",
                element_ids=[kept["id"], str(uuid4())],
                package_id=package["id"],
            )

        assert (await call(server, "get_element", element_id=kept["id"]))["package_id"] is None

    async def test_more_than_the_batch_or_none_is_refused_by_the_schema(
        self, server: MCPServer[Any]
    ) -> None:
        for ids in ([str(uuid4()) for _ in range(101)], []):
            with pytest.raises(ToolError):
                await call(server, "move_elements", element_ids=ids, package_id="root")

    async def test_a_reader_may_read_the_tree_and_nothing_more(
        self, server: MCPServer[Any]
    ) -> None:
        package = await call(server, "create_package", name="A")

        with acting_as(a_reader()):
            assert len((await call(server, "list_packages"))["result"]) == 1
            for name, arguments in (
                ("create_package", {"name": "B"}),
                ("update_package", {"package_id": package["id"], "name": "C"}),
                ("delete_package", {"package_id": package["id"]}),
                ("move_elements", {"element_ids": [str(uuid4())], "package_id": "root"}),
            ):
                with pytest.raises(ToolError):
                    await call(server, name, **arguments)

    async def test_the_catalogue_listing_takes_a_package(
        self, server: MCPServer[Any], service: ArchitectureService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = []
        real = service.list_elements

        async def spy(criteria):  # type: ignore[no-untyped-def]
            seen.append(criteria)
            return await real(criteria)

        monkeypatch.setattr(service, "list_elements", spy)
        package = await call(server, "create_package", name="A")

        await call(server, "list_elements", package_id=package["id"], include_subpackages=False)

        assert str(seen[0].package_id) == package["id"]
        assert seen[0].include_subpackages is False

    async def test_listing_without_a_package_still_works_as_before(
        self, server: MCPServer[Any]
    ) -> None:
        await an_element(server, "node", "db-01")

        page = await call(server, "list_elements")

        assert (page["total"], page["limit"], page["offset"]) == (1, 50, 0)


@pytest.mark.asyncio
class TestTheSearchTools:
    """What an agent sends reaches the repository as one `ElementFilter` — and nothing else does."""

    @pytest.fixture
    def seen(self, service: ArchitectureService, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
        criteria: list[Any] = []
        real = service.list_elements

        async def spy(found):  # type: ignore[no-untyped-def]
            criteria.append(found)
            return await real(found)

        monkeypatch.setattr(service, "list_elements", spy)
        return criteria

    async def test_every_criterion_reaches_the_filter(
        self, server: MCPServer[Any], seen: list[Any]
    ) -> None:
        package = await call(server, "create_package", name="Finance")

        page = await call(
            server,
            "search_elements",
            element_types=["application_component"],
            layers=["application"],
            text=" fact ",
            package_id=package["id"],
            include_subpackages=False,
            properties=[
                {"key": "owner", "op": "eq", "value": "finance"},
                {"key": "cidr", "op": "exists"},
            ],
            relations=[
                {
                    "relationship_type": "serving",
                    "direction": "out",
                    "other_type": "business_process",
                },
                {"relationship_type": "association", "present": False},
            ],
            order="updated_at",
            limit=10,
            offset=20,
        )

        criteria = seen[0]
        assert [t.value for t in criteria.element_types] == ["application_component"]
        assert criteria.search == "fact"
        assert str(criteria.package_id) == package["id"]
        assert criteria.include_subpackages is False
        assert [(p.key, p.op.value, p.value) for p in criteria.properties] == [
            ("owner", "eq", "finance"),
            ("cidr", "exists", None),
        ]
        assert [
            (r.relationship_type.value, r.direction.value, r.present) for r in criteria.relations
        ] == [
            ("serving", "out", True),
            ("association", "any", False),
        ]
        assert (criteria.order.value, criteria.limit, criteria.offset) == ("updated_at", 10, 20)
        assert (page["limit"], page["offset"]) == (10, 20)

    async def test_no_argument_at_all_is_the_whole_catalogue_paged(
        self, server: MCPServer[Any]
    ) -> None:
        await an_element(server, "node", "db-01")

        page = await call(server, "search_elements")

        assert (page["total"], page["limit"], page["offset"]) == (1, 50, 0)

    @pytest.mark.parametrize(
        "arguments",
        [
            {"properties": [{"key": "owner", "op": "eq"}]},
            {"properties": [{"key": "owner", "op": "exists", "value": "x"}]},
            {"properties": [{"key": "a-b", "op": "exists"}]},
            {"properties": [{"key": "owner", "op": "gt", "value": "1"}]},
            {"properties": [{"key": "owner", "op": "contains", "value": "a\x00b"}]},
            {"root_only": True, "package_id": "11111111-1111-4111-8111-111111111111"},
            {"element_types": ["microservice"]},
            {"limit": 0},
        ],
    )
    async def test_a_filter_the_server_would_refuse_is_refused_with_a_message(
        self, server: MCPServer[Any], arguments: dict[str, Any]
    ) -> None:
        with pytest.raises(ToolError):
            await call(server, "search_elements", **arguments)

    async def test_a_contradictory_filter_says_why(self, server: MCPServer[Any]) -> None:
        with pytest.raises(ToolError) as refused:
            await call(
                server,
                "search_elements",
                root_only=True,
                package_id="11111111-1111-4111-8111-111111111111",
            )

        assert "package" in str(refused.value).lower()

    async def test_a_filter_is_kept_under_a_name_and_run_again(
        self, server: MCPServer[Any], seen: list[Any]
    ) -> None:
        await an_element(server, "node", "db-01")
        saved = await call(
            server,
            "save_query",
            name="Les noeuds",
            description="Tous",
            element_types=["node"],
            properties=[{"key": "owner", "op": "absent"}],
        )

        assert saved["created_by"] == "editor"
        assert saved["query"]["element_types"] == ["node"]
        listed = (await call(server, "list_saved_queries"))["result"]
        assert [(q["name"], "query" in q) for q in listed] == [("Les noeuds", False)]

        page = await call(server, "run_saved_query", query_id=saved["id"], limit=10)

        assert (page["total"], page["limit"]) == (1, 10)
        assert [t.value for t in seen[-1].element_types] == ["node"]

    async def test_a_name_already_taken_is_told_to_the_model(self, server: MCPServer[Any]) -> None:
        await call(server, "save_query", name="A")

        with pytest.raises(ToolError, match="already named"):
            await call(server, "save_query", name="A")

    async def test_a_query_naming_a_vanished_package_says_so_and_stays(
        self, server: MCPServer[Any]
    ) -> None:
        package = await call(server, "create_package", name="Finance")
        saved = await call(server, "save_query", name="Compta", package_id=package["id"])
        await call(server, "delete_package", package_id=package["id"], move_contents_to="root")

        with pytest.raises(ToolError, match="no longer exists"):
            await call(server, "run_saved_query", query_id=saved["id"])

        assert len((await call(server, "list_saved_queries"))["result"]) == 1

    async def test_deleting_a_query_leaves_the_elements_it_found(
        self, server: MCPServer[Any]
    ) -> None:
        element = await an_element(server, "node", "db-01")
        saved = await call(server, "save_query", name="Tous")

        await call(server, "delete_saved_query", query_id=saved["id"])

        assert (await call(server, "list_saved_queries"))["result"] == []
        assert (await call(server, "get_element", element_id=element["id"]))["name"] == "db-01"

    async def test_an_unknown_query_is_a_message(self, server: MCPServer[Any]) -> None:
        for name in ("run_saved_query", "delete_saved_query"):
            with pytest.raises(ToolError, match="no saved query"):
                await call(server, name, query_id=str(uuid4()))

    async def test_a_reader_searches_lists_and_runs_but_does_not_write(
        self, server: MCPServer[Any]
    ) -> None:
        saved = await call(server, "save_query", name="A")

        with acting_as(a_reader()):
            await call(server, "search_elements")
            assert len((await call(server, "list_saved_queries"))["result"]) == 1
            await call(server, "run_saved_query", query_id=saved["id"])
            with pytest.raises(ToolError):
                await call(server, "save_query", name="B")
            with pytest.raises(ToolError):
                await call(server, "delete_saved_query", query_id=saved["id"])


@pytest.mark.asyncio
class TestTheSameBoundsAsTheHttpAdapter:
    """A tool argument and its HTTP twin must refuse exactly the same values.

    The two adapters used to declare their bounds separately, so a limit
    raised on one side would have left the other refusing — or accepting —
    what its sibling does not. This pins every pair; the tool side reads the
    schema an agent is actually sent.
    """

    @pytest.mark.parametrize(
        ("tool", "argument", "model", "field"),
        [
            ("create_element", "name", ElementCreate, "name"),
            ("create_element", "description", ElementCreate, "description"),
            ("create_element", "documentation", ElementCreate, "documentation"),
            ("update_element", "name", ElementUpdate, "name"),
            ("update_element", "description", ElementUpdate, "description"),
            ("update_element", "documentation", ElementUpdate, "documentation"),
            ("connect_elements", "name", RelationshipCreate, "name"),
            ("declare_ip_subnet", "name", SubnetCreate, "name"),
            ("declare_ip_subnet", "cidr", SubnetCreate, "cidr"),
            ("declare_ip_subnet", "vrf", SubnetCreate, "vrf"),
            ("declare_ip_subnet", "reserved", SubnetCreate, "reserved"),
            ("declare_ip_subnet", "description", SubnetCreate, "description"),
            ("assign_ip_address", "address", AddressAssign, "address"),
            ("assign_ip_address", "vrf", AddressAssign, "vrf"),
            ("locate_ip_address", "address", AddressAssign, "address"),
            ("set_file_details", "title", FileDetailsWrite, "title"),
            ("set_file_details", "description", FileDetailsWrite, "description"),
            ("set_file_details", "tags", FileDetailsWrite, "tags"),
            ("upload_file", "title", FileDetailsWrite, "title"),
            ("upload_file", "description", FileDetailsWrite, "description"),
            ("upload_file", "tags", FileDetailsWrite, "tags"),
            ("create_package", "name", PackageCreate, "name"),
            ("create_package", "description", PackageCreate, "description"),
            ("update_package", "name", PackageUpdate, "name"),
            ("update_package", "description", PackageUpdate, "description"),
            ("move_elements", "element_ids", MoveElements, "element_ids"),
            ("save_query", "name", SavedQueryCreate, "name"),
            ("save_query", "description", SavedQueryCreate, "description"),
            ("search_elements", "text", ElementQuerySchema, "text"),
            ("search_elements", "limit", ElementQuerySchema, "limit"),
            ("search_elements", "offset", ElementQuerySchema, "offset"),
            ("search_elements", "properties", ElementQuerySchema, "properties"),
            ("search_elements", "relations", ElementQuerySchema, "relations"),
            ("save_query", "text", ElementQuerySchema, "text"),
            ("save_query", "properties", ElementQuerySchema, "properties"),
            ("save_query", "relations", ElementQuerySchema, "relations"),
            ("create_baseline", "name", BaselineCreate, "name"),
            ("create_baseline", "description", BaselineCreate, "description"),
            ("restore_baseline", "expected_digest", RestoreRequest, "expected_digest"),
            ("transition_element", "expected_revision", ApprovalRequest, "expected_revision"),
        ],
    )
    async def test_a_body_field_and_its_tool_argument_agree(
        self,
        server: MCPServer[Any],
        tool: str,
        argument: str,
        model: type[Any],
        field: str,
    ) -> None:
        tools = {listed.name: listed for listed in await server.list_tools()}
        sent_to_the_agent = bounds_of(tools[tool].input_schema["properties"][argument])
        served_over_http = bounds_of(model.model_json_schema()["properties"][field])

        assert sent_to_the_agent, f"{tool}.{argument} carries no bound at all"
        assert sent_to_the_agent == served_over_http

    @pytest.mark.parametrize(
        ("tool", "argument", "path", "parameter"),
        [
            ("list_elements", "limit", "/elements", "limit"),
            ("list_elements", "offset", "/elements", "offset"),
            ("list_elements", "search", "/elements", "search"),
            ("list_relationships", "limit", "/relationships", "limit"),
            ("list_relationships", "offset", "/relationships", "offset"),
            ("neighbourhood", "depth", "/elements/{element_id}/neighbourhood", "depth"),
            ("impact_of", "depth", "/elements/{element_id}/impact", "depth"),
            ("list_ip_addresses", "search", "/ipam/addresses", "search"),
            ("list_files", "prefix", "/files", "prefix"),
            ("read_file", "key", "/files/content", "key"),
            ("describe_file", "key", "/files/metadata", "key"),
            ("run_saved_query", "limit", "/queries/{query_id}/elements", "limit"),
            ("run_saved_query", "offset", "/queries/{query_id}/elements", "offset"),
            ("list_audit_events", "actor", "/audit", "actor"),
            ("list_audit_events", "request_id", "/audit", "request_id"),
            ("list_audit_events", "cursor", "/audit", "cursor"),
            ("list_audit_events", "limit", "/audit", "limit"),
            ("get_element_history", "limit", "/elements/{element_id}/history", "limit"),
            ("get_element_history", "cursor", "/elements/{element_id}/history", "cursor"),
            ("diff_baselines", "against", "/baselines/{baseline_id}/diff", "against"),
            ("check_model", "limit", "/quality", "limit"),
            ("check_model", "cursor", "/quality", "cursor"),
        ],
    )
    async def test_a_query_parameter_and_its_tool_argument_agree(
        self,
        server: MCPServer[Any],
        service: ArchitectureService,
        tool: str,
        argument: str,
        path: str,
        parameter: str,
    ) -> None:
        openapi = create_app(Settings(debug=True), architecture_service=service).openapi()
        tools = {listed.name: listed for listed in await server.list_tools()}
        sent_to_the_agent = bounds_of(tools[tool].input_schema["properties"][argument])
        served_over_http = bounds_of(query_parameter(openapi, path, parameter))

        assert sent_to_the_agent, f"{tool}.{argument} carries no bound at all"
        assert sent_to_the_agent == served_over_http

    async def test_the_package_target_is_the_same_type_over_http_and_mcp(
        self, server: MCPServer[Any], service: ArchitectureService
    ) -> None:
        openapi = create_app(Settings(debug=True), architecture_service=service).openapi()
        operation = openapi["paths"]["/packages/{package_id}"]["delete"]
        over_http = next(
            p["schema"] for p in operation["parameters"] if p["name"] == "move_contents_to"
        )
        tools = {listed.name: listed for listed in await server.list_tools()}
        sent = tools["delete_package"].input_schema["properties"]["move_contents_to"]

        assert bounds_of(sent), "move_contents_to carries no bound at all"
        assert bounds_of(sent) == bounds_of(over_http)
        filing = tools["move_elements"].input_schema["properties"]["package_id"]
        assert bounds_of(filing) == bounds_of(over_http)


DATA_XML = (DATA / "minimal-model.xml").read_text(encoding="utf-8")


@pytest.mark.asyncio
class TestExportModel:
    async def test_it_returns_the_model_as_xml_text(self, server: MCPServer[Any]) -> None:
        await an_element(server, "node", "srv-1")

        text = (await call(server, "export_model"))["result"]

        assert text.startswith("<?xml") and "srv-1" in text

    async def test_csv_comes_back_as_three_labelled_files(self, server: MCPServer[Any]) -> None:
        await an_element(server, "node", "srv-1")

        text = (await call(server, "export_model", format="csv"))["result"]

        assert text.index("--- elements.csv ---") < text.index("--- relations.csv ---")
        assert text.index("--- relations.csv ---") < text.index("--- properties.csv ---")
        assert "srv-1" in text and "PK" not in text[:2]  # text, not a zip

    async def test_views_are_never_exported_over_mcp(
        self, server: MCPServer[Any], diagram_service: DiagramService
    ) -> None:
        await diagram_service.create(name="Vue")

        text = (await call(server, "export_model"))["result"]

        assert "<views>" not in text

    async def test_a_model_over_the_ceiling_is_a_tool_error_that_names_the_screen(
        self, server: MCPServer[Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for number in range(30):
            await an_element(server, "node", f"srv-{number}")
        monkeypatch.setattr("ea.mcp.server.MAX_MCP_EXPORT_BYTES", 500)

        for fmt in ("archimate-xml", "csv"):
            with pytest.raises(ToolError, match="Échanges"):
                await call(server, "export_model", format=fmt)

    async def test_the_ceiling_stops_the_rendering_not_only_the_answer(
        self,
        server: MCPServer[Any],
        exchange_store: InMemoryExchange,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for number in range(30):
            await an_element(server, "node", f"srv-{number}")
        monkeypatch.setattr("ea.mcp.server.MAX_MCP_EXPORT_BYTES", 500)
        pages: list[int] = []
        original = exchange_store.snapshot

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def watching(scope):  # type: ignore[no-untyped-def]
            async with original(scope) as opened:
                page = opened.elements_page

                async def counted(offset, limit):  # type: ignore[no-untyped-def]
                    pages.append(offset)
                    return await page(offset, limit)

                opened.elements_page = counted
                yield opened

        monkeypatch.setattr(exchange_store, "snapshot", watching)

        with pytest.raises(ToolError):
            await call(server, "export_model")

        assert len(pages) <= 2  # it did not render a model it would refuse to return

    async def test_a_reader_may_export(self, server: MCPServer[Any]) -> None:
        await an_element(server, "node", "srv-1")

        with acting_as(a_reader()):
            assert "srv-1" in (await call(server, "export_model"))["result"]

    async def test_nobody_may_export(self, server: MCPServer[Any], nobody_calling: None) -> None:
        with pytest.raises(ToolError):
            await call(server, "export_model")

    async def test_a_scope_without_its_id_is_a_tool_error_one_can_act_on(
        self, server: MCPServer[Any]
    ) -> None:
        with pytest.raises(ToolError, match="package_id"):
            await call(server, "export_model", scope="package")
        with pytest.raises(ToolError, match="query_id"):
            await call(server, "export_model", scope="query")

    async def test_an_unknown_package_is_an_unknown_scope(self, server: MCPServer[Any]) -> None:
        with pytest.raises(ToolError, match="no such package"):
            await call(server, "export_model", scope="package", package_id=str(uuid4()))

    async def test_a_package_scope_exports_the_package(
        self, server: MCPServer[Any], package_service: PackageService
    ) -> None:
        made = await call(server, "create_element", element_type="node", name="in")
        await an_element(server, "node", "out")
        package = (await package_service.create(name="Top")).package.id
        await package_service.move_elements([UUID(made["id"])], package)

        text = (await call(server, "export_model", scope="package", package_id=str(package)))[
            "result"
        ]

        assert ">in<" in text and ">out<" not in text

    async def test_it_is_a_read(self, server: MCPServer[Any]) -> None:
        tool = next(t for t in await server.list_tools() if t.name == "export_model")

        assert tool.annotations is not None and tool.annotations.read_only_hint is True

    async def test_it_holds_no_connection_nor_place_once_it_has_answered(
        self, server: MCPServer[Any], exchange_service: ExchangeService
    ) -> None:
        await an_element(server, "node", "srv-1")

        for _ in range(10):
            await call(server, "export_model")

        assert exchange_service._exports.in_flight == 0


@pytest.mark.asyncio
class TestImportModel:
    async def test_the_default_is_a_dry_run_that_writes_nothing(
        self,
        server: MCPServer[Any],
        repository: InMemoryRepository,
        audit_log: InMemoryAuditLog,
    ) -> None:
        events = len(audit_log.events)

        report = await call(server, "import_model", content=DATA_XML)

        assert report["dry_run"] is True and report["applied"] is False
        assert report["counts"]["elements"]["created"] == 3
        assert (repository.elements, repository.relationships) == ({}, {})
        assert len(audit_log.events) == events

    async def test_the_schema_the_agent_is_sent_says_dry_run_is_true_by_default(
        self, server: MCPServer[Any]
    ) -> None:
        tool = next(t for t in await server.list_tools() if t.name == "import_model")

        assert tool.input_schema["properties"]["dry_run"]["default"] is True
        assert "dry_run" not in tool.input_schema.get("required", [])

    async def test_a_dry_run_value_nobody_can_read_writes_nothing(
        self, server: MCPServer[Any], repository: InMemoryRepository
    ) -> None:
        for bad in ("maybe", "", 2, [], {}):
            with pytest.raises(ToolError):
                await call(server, "import_model", content=DATA_XML, dry_run=bad)

        assert repository.elements == {}

    async def test_dry_run_false_writes(
        self, server: MCPServer[Any], repository: InMemoryRepository
    ) -> None:
        report = await call(server, "import_model", content=DATA_XML, dry_run=False)

        assert report["applied"] is True and len(repository.elements) == 3

    async def test_csv_takes_its_three_texts(
        self, server: MCPServer[Any], repository: InMemoryRepository
    ) -> None:
        report = await call(
            server,
            "import_model",
            format="csv",
            elements='"ID","Type","Name"\r\n"a","Node","srv"\r\n',
            relations='"ID","Type","Source","Target"\r\n',
            dry_run=False,
        )

        assert report["format"] == "csv" and report["counts"]["elements"]["created"] == 1

    async def test_views_are_not_imported_over_mcp(
        self, server: MCPServer[Any], diagrams: InMemoryDiagrams
    ) -> None:
        text = (DATA / "views-model.xml").read_text(encoding="utf-8")

        report = await call(server, "import_model", content=text, dry_run=False)

        assert report["counts"]["views"]["ignored"] == 1 and diagrams.diagrams == {}

    async def test_a_reader_sees_the_tool_and_cannot_even_simulate(
        self, server: MCPServer[Any], repository: InMemoryRepository
    ) -> None:
        assert "import_model" in {t.name for t in await server.list_tools()}

        with acting_as(a_reader()), pytest.raises(ToolError, match="role"):
            await call(server, "import_model", content=DATA_XML)

        assert repository.elements == {}

    async def test_the_text_a_format_needs_is_required(self, server: MCPServer[Any]) -> None:
        with pytest.raises(ToolError, match="content"):
            await call(server, "import_model")
        with pytest.raises(ToolError, match="elements"):
            await call(server, "import_model", format="csv", content="x")

    async def test_text_over_the_ceiling_is_refused_before_it_is_encoded_or_read(
        self, server: MCPServer[Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("ea.mcp.server.MAX_MCP_UPLOAD_BYTES", 50)
        encoded: list[str] = []
        original = str.encode  # (a str method cannot be patched: the refusal is on the length)

        for arguments in (
            {"content": DATA_XML},
            {"format": "csv", "elements": "a" * 30, "relations": "b" * 30},  # the sum counts
        ):
            with pytest.raises(ToolError, match="50 bytes") as raised:
                await call(server, "import_model", **arguments)
            assert "Échanges" in str(raised.value)

        assert encoded == [] and original

    async def test_multibyte_text_is_measured_in_bytes(
        self, server: MCPServer[Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("ea.mcp.server.MAX_MCP_UPLOAD_BYTES", 60)
        xml = DATA_XML.replace("Customer", "é" * 20)  # few characters, many bytes

        with pytest.raises(ToolError, match="60 bytes"):
            await call(server, "import_model", content=xml[:30] + "é" * 30)

    async def test_a_malformed_file_is_a_tool_error_with_no_echo(
        self, server: MCPServer[Any]
    ) -> None:
        for content, secret in (("<model><secret-tag>", "secret-tag"), ("\x00LEAK", "LEAK")):
            with pytest.raises(ToolError) as raised:
                await call(server, "import_model", content=content)
            assert secret not in str(raised.value)

    async def test_the_domain_failures_an_agent_can_act_on_have_their_own_words(
        self, server: MCPServer[Any]
    ) -> None:
        with pytest.raises(ToolError, match="package"):
            await call(server, "import_model", content=DATA_XML, target_package_id=str(uuid4()))

    async def test_it_writes_but_never_deletes(self, server: MCPServer[Any]) -> None:
        tool = next(t for t in await server.list_tools() if t.name == "import_model")

        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.destructive_hint is False
        description = tool.description or ""
        assert "dry_run" in description and "show" in description.lower()

    async def test_the_arguments_are_bounded_by_the_one_ceiling_of_the_module(
        self, server: MCPServer[Any]
    ) -> None:
        from ea.mcp.server import MAX_MCP_UPLOAD_BYTES

        tool = next(t for t in await server.list_tools() if t.name == "import_model")
        properties = tool.input_schema["properties"]

        for argument in ("content", "elements", "relations", "properties"):
            lengths = [branch.get("maxLength") for branch in properties[argument]["anyOf"]]
            assert MAX_MCP_UPLOAD_BYTES in lengths
        assert MAX_MCP_UPLOAD_BYTES == 1024 * 1024  # an MCP-only ceiling, no HTTP twin: pinned here
        assert properties["target_package_id"]["anyOf"][0]["format"] == "uuid"

    async def test_the_instructions_tell_a_model_to_simulate_first_and_to_show_the_report(
        self,
    ) -> None:
        assert "dry_run" in INSTRUCTIONS and "export_model" in INSTRUCTIONS
        assert "report" in INSTRUCTIONS and "1 MB" in INSTRUCTIONS and "safe_cells" in INSTRUCTIONS

    async def test_the_names_of_the_file_are_not_in_the_logs(
        self, server: MCPServer[Any], caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        marker = "SECRET-MARKER-MCP"
        xml = DATA_XML.replace("Customer", marker)

        with caplog.at_level(logging.DEBUG, logger="ea"):
            await call(server, "import_model", content=xml, dry_run=False)
            with pytest.raises(ToolError):
                await call(server, "import_model", content=xml + "<")

        assert marker not in caplog.text


@pytest.mark.asyncio
class TestAnImportsTextIsNeverQuoted:
    """A refused argument is described by its name and its rule, never by its value."""

    @pytest.mark.parametrize(
        "bad",
        ["SECRET-" + "x" * (1024 * 1024 + 1), "SECRET-\ud800-lone-surrogate"],
        ids=["too-long", "lone-surrogate"],
    )
    @pytest.mark.parametrize("argument", ["content", "elements", "relations", "properties"])
    async def test_neither_the_error_nor_the_logs_quote_the_text(
        self,
        server: MCPServer[Any],
        caplog: pytest.LogCaptureFixture,
        argument: str,
        bad: str,
    ) -> None:
        import logging

        with caplog.at_level(logging.DEBUG), pytest.raises(ToolError) as refused:
            await server.call_tool("import_model", {argument: bad})

        assert "SECRET" not in str(refused.value)
        assert "SECRET" not in caplog.text

    async def test_a_reader_is_refused_before_the_file_is_measured(
        self, server: MCPServer[Any]
    ) -> None:
        # 600 000 characters, 1 200 000 bytes: inside the schema, over the byte ceiling.
        big = "é" * 600_000
        with acting_as(a_reader()), pytest.raises(ToolError) as refused:
            await server.call_tool("import_model", {"content": big})
        assert "at most" not in str(refused.value)


@pytest.mark.asyncio
class TestTheRelationshipMatrixTool:
    async def test_it_answers_with_the_grid_and_the_links(self, server: MCPServer[Any]) -> None:
        api = await an_element(server, "application_service", "Invoice API")
        process = await an_element(server, "business_process", "Order to cash")
        await call(
            server,
            "connect_elements",
            relationship_type="serving",
            source_id=api["id"],
            target_id=process["id"],
        )

        grid = await call(
            server,
            "get_relationship_matrix",
            rows={"element_ids": [api["id"]]},
            cols={"element_ids": [process["id"]]},
        )

        assert [c["links"][0]["relationship_type"] for c in grid["cells"]] == ["serving"]

    async def test_it_only_reads(self, server: MCPServer[Any]) -> None:
        tool = next(t for t in await server.list_tools() if t.name == "get_relationship_matrix")

        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True

    async def test_an_unknown_element_is_a_message_the_model_can_act_on(
        self, server: MCPServer[Any]
    ) -> None:
        with pytest.raises(ToolError, match="no element with id"):
            await call(
                server,
                "get_relationship_matrix",
                rows={"element_ids": ["00000000-0000-4000-8000-000000000001"]},
                cols={},
            )

    async def test_too_many_named_elements_are_refused_with_the_ceiling(
        self, server: MCPServer[Any]
    ) -> None:
        ids = [f"00000000-0000-4000-8000-{n:012d}" for n in range(51)]

        with pytest.raises(ToolError, match="at most 50"):
            await call(server, "get_relationship_matrix", rows={"element_ids": ids}, cols={})

    async def test_its_axis_bounds_are_the_http_ones(self, server: MCPServer[Any]) -> None:
        tool = next(t for t in await server.list_tools() if t.name == "get_relationship_matrix")
        sent = tool.input_schema["$defs"]["AxisSelect"]["properties"]["element_ids"]
        served = AxisSelect.model_json_schema()["properties"]["element_ids"]

        assert bounds_of(sent) == bounds_of(served)
        assert bounds_of(sent)["maxItems"] == 500


@pytest.mark.asyncio
class TestCheckModel:
    async def test_it_reports_what_a_quality_run_finds(self, server: MCPServer[Any]) -> None:
        await an_element(server, "node", "Loner")

        report = await call(server, "check_model")

        assert "orphan" in {f["rule"] for f in report["findings"]}
        assert report["total"] == len(report["findings"])

    async def test_it_filters_by_code(self, server: MCPServer[Any]) -> None:
        await an_element(server, "node", "Loner")

        report = await call(server, "check_model", code="orphan")

        assert {f["rule"] for f in report["findings"]} == {"orphan"}

    async def test_it_only_reads(self, server: MCPServer[Any]) -> None:
        tool = next(t for t in await server.list_tools() if t.name == "check_model")

        assert tool.annotations is not None and tool.annotations.read_only_hint is True

    async def test_its_description_says_when_to_call_it(self, server: MCPServer[Any]) -> None:
        tool = next(t for t in await server.list_tools() if t.name == "check_model")

        assert "after an import" in (tool.description or "")

    async def test_an_unknown_rule_code_is_refused(self, server: MCPServer[Any]) -> None:
        with pytest.raises(ToolError):
            await call(server, "check_model", code="nope")

    async def test_a_limit_over_the_ceiling_is_refused(self, server: MCPServer[Any]) -> None:
        with pytest.raises(ToolError):
            await call(server, "check_model", limit=1001)
