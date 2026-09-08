"""The IPAM endpoints, end to end against a doubled graph.

The contract: status codes, the error envelope, and the shape of a payload.
These are the same rules `tests/unit/test_ipam_service.py` covers, seen from
outside — what matters here is that each refusal arrives with a code a client
can branch on, rather than as one undifferentiated 409.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import InMemoryRepository


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, repository: InMemoryRepository
) -> AsyncIterator[httpx.AsyncClient]:
    """The app over the in-memory graph, which answers both repository ports."""
    app = create_app(Settings(debug=True), architecture_service=service, ipam=repository)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def a_subnet(
    client: httpx.AsyncClient, cidr: str = "10.0.1.0/24", **extra: str
) -> dict[str, Any]:
    payload = {"name": extra.pop("name", cidr), "cidr": cidr, **extra}
    response = await client.post("/ipam/subnets", json=payload)
    assert response.status_code == 201, response.text
    return dict(response.json())


async def a_host(client: httpx.AsyncClient, name: str = "srv-app-01") -> dict[str, Any]:
    response = await client.post("/elements", json={"element_type": "node", "name": name})
    assert response.status_code == 201, response.text
    return dict(response.json())


@pytest.mark.asyncio
class TestSubnets:
    async def test_a_declared_subnet_is_an_element_of_the_catalogue(
        self, client: httpx.AsyncClient
    ) -> None:
        subnet = await a_subnet(client, "10.0.1.0/24", name="DMZ", vrf="dmz")

        element = await client.get(f"/elements/{subnet['element_id']}")
        assert element.json()["element_type"] == "communication_network"
        assert element.json()["properties"] == {"cidr": "10.0.1.0/24", "vrf": "dmz"}

    async def test_a_subnet_reports_four_counts_and_not_a_percentage(
        self, client: httpx.AsyncClient
    ) -> None:
        subnet = await a_subnet(client, "10.0.1.0/24", name="DMZ", reserved="10.0.1.1")

        assert (subnet["capacity"], subnet["reserved"], subnet["used"], subnet["free"]) == (
            254,
            1,
            0,
            253,
        )

    async def test_the_same_prefix_twice_in_one_scope_is_a_conflict(
        self, client: httpx.AsyncClient
    ) -> None:
        await a_subnet(client, "10.0.1.0/24", name="DMZ")

        response = await client.post(
            "/ipam/subnets", json={"name": "DMZ bis", "cidr": "10.0.1.0/24"}
        )

        assert response.status_code == 409
        assert response.json()["error"] == "duplicate"

    async def test_a_prefix_with_host_bits_is_refused_and_names_the_one_meant(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post("/ipam/subnets", json={"name": "DMZ", "cidr": "10.0.1.5/24"})

        assert response.status_code == 422
        assert "10.0.1.0/24" in response.json()["detail"]

    async def test_asking_for_the_detail_of_something_that_is_not_a_subnet(
        self, client: httpx.AsyncClient
    ) -> None:
        host = await a_host(client)

        response = await client.get(f"/ipam/subnets/{host['id']}")

        assert response.status_code == 422
        assert response.json()["error"] == "not_a_subnet"

    async def test_listing_can_be_narrowed_to_one_scope(self, client: httpx.AsyncClient) -> None:
        await a_subnet(client, "10.0.1.0/24", name="Client A", vrf="a")
        await a_subnet(client, "10.0.2.0/24", name="Client B", vrf="b")

        response = await client.get("/ipam/subnets", params={"vrf": "b"})

        assert [subnet["name"] for subnet in response.json()] == ["Client B"]


@pytest.mark.asyncio
class TestAssigningAndAllocating:
    async def test_an_allocated_address_comes_back_with_its_element(
        self, client: httpx.AsyncClient
    ) -> None:
        subnet = await a_subnet(client)
        host = await a_host(client)

        response = await client.post(
            f"/ipam/subnets/{subnet['element_id']}/allocate", json={"element_id": host["id"]}
        )

        assert response.status_code == 201
        body = response.json()
        assert (body["address"], body["element_name"]) == ("10.0.1.1", "srv-app-01")
        assert body["subnet_id"] == subnet["element_id"]

    async def test_a_taken_address_is_a_conflict_with_its_own_code(
        self, client: httpx.AsyncClient
    ) -> None:
        await a_subnet(client)
        first = await a_host(client, "srv-app-01")
        second = await a_host(client, "srv-app-02")
        await client.post(
            "/ipam/addresses", json={"element_id": first["id"], "address": "10.0.1.12"}
        )

        response = await client.post(
            "/ipam/addresses", json={"element_id": second["id"], "address": "10.0.1.12"}
        )

        assert response.status_code == 409
        assert response.json()["error"] == "address_taken"

    async def test_an_address_no_subnet_holds_says_to_declare_the_subnet(
        self, client: httpx.AsyncClient
    ) -> None:
        host = await a_host(client)

        response = await client.post(
            "/ipam/addresses", json={"element_id": host["id"], "address": "192.168.4.1"}
        )

        assert response.status_code == 422
        assert response.json()["error"] == "address_outside_any_network"
        assert "declare the subnet" in response.json()["detail"]

    async def test_an_element_that_cannot_answer_on_an_address_is_refused(
        self, client: httpx.AsyncClient
    ) -> None:
        await a_subnet(client)
        response = await client.post(
            "/elements", json={"element_type": "business_process", "name": "Facturer"}
        )
        process = response.json()

        refused = await client.post(
            "/ipam/addresses", json={"element_id": process["id"], "address": "10.0.1.12"}
        )

        assert refused.status_code == 422
        assert refused.json()["error"] == "not_addressable"

    async def test_a_full_subnet_is_a_conflict_a_client_can_branch_on(
        self, client: httpx.AsyncClient
    ) -> None:
        subnet = await a_subnet(client, "10.0.1.0/30", name="Lien")
        for name in ("a", "b"):
            host = await a_host(client, name)
            await client.post(
                f"/ipam/subnets/{subnet['element_id']}/allocate", json={"element_id": host["id"]}
            )
        latecomer = await a_host(client, "c")

        response = await client.post(
            f"/ipam/subnets/{subnet['element_id']}/allocate",
            json={"element_id": latecomer["id"]},
        )

        assert response.status_code == 409
        assert response.json()["error"] == "network_exhausted"

    async def test_allocating_for_an_unknown_element_is_a_not_found(
        self, client: httpx.AsyncClient
    ) -> None:
        subnet = await a_subnet(client)

        response = await client.post(
            f"/ipam/subnets/{subnet['element_id']}/allocate", json={"element_id": str(uuid4())}
        )

        assert response.status_code == 404


@pytest.mark.asyncio
class TestLookingUpAndReleasing:
    async def test_an_address_answers_with_its_element_and_that_element_s_links(
        self, client: httpx.AsyncClient
    ) -> None:
        await a_subnet(client)
        host = await a_host(client)
        billing = await client.post(
            "/elements", json={"element_type": "application_component", "name": "Billing"}
        )
        await client.post(
            "/ipam/addresses", json={"element_id": host["id"], "address": "10.0.1.12"}
        )
        await client.post(
            "/relationships",
            json={
                "relationship_type": "serving",
                "source_id": host["id"],
                "target_id": billing.json()["id"],
            },
        )

        response = await client.get("/ipam/addresses/10.0.1.12")

        assert response.status_code == 200
        body = response.json()
        assert body["address"]["element_name"] == "srv-app-01"
        assert {element["name"] for element in body["graph"]["elements"]} == {
            "srv-app-01",
            "Billing",
        }

    async def test_an_address_nothing_holds_is_a_not_found(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/ipam/addresses/10.0.1.12")

        assert response.status_code == 404
        assert response.json()["error"] == "not_found"

    async def test_releasing_leaves_the_element_and_frees_the_address(
        self, client: httpx.AsyncClient
    ) -> None:
        await a_subnet(client)
        host = await a_host(client)
        await client.post(
            "/ipam/addresses", json={"element_id": host["id"], "address": "10.0.1.12"}
        )

        released = await client.delete(f"/ipam/elements/{host['id']}/address")

        assert released.status_code == 204
        assert (await client.get(f"/elements/{host['id']}")).json()["properties"] == {
            "vrf": "default"
        }
        assert (await client.get("/ipam/addresses/10.0.1.12")).status_code == 404

    async def test_deleting_the_element_takes_its_address_with_it(
        self, client: httpx.AsyncClient
    ) -> None:
        """The address *is* the element, so nothing has to cascade it away."""
        await a_subnet(client)
        host = await a_host(client)
        await client.post(
            "/ipam/addresses", json={"element_id": host["id"], "address": "10.0.1.12"}
        )

        await client.delete(f"/elements/{host['id']}")

        assert (await client.get("/ipam/addresses/10.0.1.12")).status_code == 404
        assert (await client.get("/ipam/addresses")).json() == []


@pytest.mark.asyncio
class TestTheOtherDoorOntoTheSameProperties:
    """`PATCH /elements/{id}` writes free-form properties, and the addressing
    lives in them — so the convention is checked there too (docs/adr/0020)."""

    async def test_an_address_cannot_be_smuggled_onto_a_business_process(
        self, client: httpx.AsyncClient
    ) -> None:
        created = await client.post(
            "/elements", json={"element_type": "business_process", "name": "Facturer"}
        )

        response = await client.patch(
            f"/elements/{created.json()['id']}",
            json={"properties": {"ip_address": "10.0.1.12"}},
        )

        assert response.status_code == 422
        assert response.json()["error"] == "not_addressable"

    async def test_a_malformed_address_never_reaches_the_graph(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/elements",
            json={
                "element_type": "node",
                "name": "srv-app-01",
                "properties": {"ip_address": "10.0.1.300"},
            },
        )

        assert response.status_code == 422
        assert "not an IP address" in response.json()["detail"]
