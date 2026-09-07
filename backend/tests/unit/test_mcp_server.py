"""The MCP adapter's tools, called directly against a doubled graph.

This is the analogue of `tests/e2e/test_architecture_api.py` for the other
adapter: what a tool answers, and what it refuses. The tools are called through
`MCPServer.call_tool`, which is the same entry point the transport uses, so a
tool that is registered wrongly fails here rather than at the first agent that
tries it. The HTTP side is covered in `tests/e2e/test_mcp_endpoint.py`.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ea.mcp import build_mcp_server
from ea.services.architecture import ArchitectureService


@pytest.fixture
def server(service: ArchitectureService) -> MCPServer[Any]:
    """The adapter over the in-memory graph — no database, no transport."""
    return build_mcp_server(lambda: service)


async def call(server: MCPServer[Any], tool: str, **arguments: Any) -> Any:
    """Call a tool and hand back the structured payload an agent would read."""
    result = await server.call_tool(tool, arguments)
    assert not result.is_error, result.content
    return result.structured_content


async def an_element(server: MCPServer[Any], element_type: str, name: str) -> Any:
    return await call(server, "create_element", element_type=element_type, name=name)


@pytest.mark.asyncio
class TestTheToolset:
    async def test_it_exposes_the_whole_service_and_nothing_else(
        self, server: MCPServer[Any]
    ) -> None:
        """The list is asserted whole: a tool added without a test is a failure."""
        names = {tool.name for tool in await server.list_tools()}

        assert names == {
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
        }

    async def test_every_tool_says_whether_it_writes(self, server: MCPServer[Any]) -> None:
        """An agent decides whether to ask first from the hint, so it must be set."""
        for tool in await server.list_tools():
            assert tool.annotations is not None, tool.name
            assert tool.annotations.read_only_hint is not None, tool.name

    async def test_the_two_deletions_are_flagged_destructive(self, server: MCPServer[Any]) -> None:
        annotations = {
            tool.name: tool.annotations
            for tool in await server.list_tools()
            if tool.annotations is not None
        }

        for name in ("delete_element", "disconnect_elements"):
            assert annotations[name].read_only_hint is False, name
            assert annotations[name].destructive_hint is True, name

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
        """The bound is the one the Cypher is written against, not a suggestion."""
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
