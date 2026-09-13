"""The MCP adapter's tools, called directly against a doubled graph.

This is the analogue of `tests/e2e/test_architecture_api.py` for the other
adapter: what a tool answers, and what it refuses. The tools are called through
`MCPServer.call_tool`, which is the same entry point the transport uses, so a
tool that is registered wrongly fails here rather than at the first agent that
tries it. The HTTP side is covered in `tests/e2e/test_mcp_endpoint.py`.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ea.api.schemas import (
    AddressAssign,
    ElementCreate,
    ElementUpdate,
    RelationshipCreate,
    SubnetCreate,
)
from ea.core.config import Settings
from ea.main import create_app
from ea.mcp import build_mcp_server
from ea.services.architecture import ArchitectureService
from ea.services.documents import DocumentService
from ea.services.ipam import IpamService


@pytest.fixture
def server(
    service: ArchitectureService, document_service: DocumentService, ipam: IpamService
) -> MCPServer[Any]:
    """The adapter over the in-memory stores — no database, no transport."""
    return build_mcp_server(lambda: service, lambda: document_service, lambda: ipam)


@pytest.fixture
def server_without_an_index(
    service: ArchitectureService,
    document_service_without_an_index: DocumentService,
    ipam: IpamService,
) -> MCPServer[Any]:
    """The same adapter on a deployment with `EA_EMBEDDINGS_ENABLED` off."""
    return build_mcp_server(
        lambda: service, lambda: document_service_without_an_index, lambda: ipam
    )


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

    async def test_deleting_the_element_takes_its_documents_with_it(
        self, server: MCPServer[Any]
    ) -> None:
        """The cascade no foreign key declares, seen from the agent's side."""
        element = await an_element(server, "application_component", "Billing")
        stored = await call(
            server,
            "attach_document",
            element_id=element["id"],
            filename="runbook.md",
            content="# Runbook\n",
        )

        await call(server, "delete_element", element_id=element["id"])

        with pytest.raises(ToolError):
            await call(server, "read_document", document_id=stored["id"])


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


#: The JSON-schema keywords that *bound* a value. Descriptions are left out on
#: purpose: the model reads a tool's, a developer reads an endpoint's, and they
#: are allowed to say different things about the same field.
BOUNDS = ("minLength", "maxLength", "minimum", "maximum", "pattern")


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
