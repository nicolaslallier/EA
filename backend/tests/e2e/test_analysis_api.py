"""The matrix endpoints, seen from outside: status codes, envelopes, who may call."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.domain.matrix import MAX_MATRIX_AXIS
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import InMemoryRepository, StaticVerifier, a_reader, an_empty_package_tree


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, repository: InMemoryRepository
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        analysis=repository,
        packages=an_empty_package_tree(),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def an_element(client: httpx.AsyncClient, element_type: str, name: str) -> dict[str, Any]:
    response = await client.post("/elements", json={"element_type": element_type, "name": name})
    assert response.status_code == 201, response.text
    return dict(response.json())


@pytest.mark.asyncio
class TestTheMatrix:
    async def test_a_link_shows_in_its_cell(self, client: httpx.AsyncClient) -> None:
        api = await an_element(client, "application_service", "Invoice API")
        process = await an_element(client, "business_process", "Order to cash")
        created = await client.post(
            "/relationships",
            json={
                "relationship_type": "serving",
                "source_id": api["id"],
                "target_id": process["id"],
            },
        )
        assert created.status_code == 201

        response = await client.post(
            "/matrix",
            json={"rows": {"element_ids": [api["id"]]}, "cols": {"element_ids": [process["id"]]}},
        )

        body = response.json()
        assert response.status_code == 200
        assert [r["name"] for r in body["rows"]] == ["Invoice API"]
        assert [(c["source_id"], c["target_id"]) for c in body["cells"]] == [
            (api["id"], process["id"])
        ]
        assert body["cells"][0]["links"][0]["relationship_type"] == "serving"
        assert body["truncated"] is False

    async def test_an_empty_catalogue_is_an_empty_grid(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/matrix", json={"rows": {}, "cols": {}})

        assert response.status_code == 200
        assert response.json()["cells"] == []

    async def test_named_elements_over_the_ceiling_get_a_typed_422(
        self, client: httpx.AsyncClient
    ) -> None:
        ids = [f"00000000-0000-4000-8000-{n:012d}" for n in range(MAX_MATRIX_AXIS + 1)]

        response = await client.post("/matrix", json={"rows": {"element_ids": ids}, "cols": {}})

        assert response.status_code == 422
        assert response.json()["error"] == "matrix_axis_too_large"

    async def test_a_catalogue_axis_over_the_ceiling_is_truncated(
        self, client: httpx.AsyncClient
    ) -> None:
        for index in range(MAX_MATRIX_AXIS + 2):
            await an_element(client, "node", f"node-{index:03}")

        body = (await client.post("/matrix", json={"rows": {}, "cols": {}})).json()

        assert body["truncated"] is True
        assert len(body["rows"]) == MAX_MATRIX_AXIS
        assert body["rows_total"] == MAX_MATRIX_AXIS + 2

    async def test_an_unknown_element_is_a_404(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/matrix",
            json={"rows": {"element_ids": ["00000000-0000-4000-8000-000000000001"]}, "cols": {}},
        )

        assert response.status_code == 404

    async def test_an_unknown_field_is_refused(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/matrix", json={"rows": {"package_id": "x"}, "cols": {}})

        assert response.status_code == 422


@pytest.mark.asyncio
class TestOneCell:
    async def test_it_offers_only_what_the_metamodel_permits(
        self, client: httpx.AsyncClient
    ) -> None:
        data = await an_element(client, "data_object", "Invoice")
        process = await an_element(client, "business_process", "Order")

        response = await client.get(
            "/matrix/cell", params={"source": data["id"], "target": process["id"]}
        )

        assert response.status_code == 200
        assert "serving" not in response.json()["allowed"]
        assert "association" in response.json()["allowed"]

    async def test_creating_a_link_the_cell_did_not_offer_is_refused_by_the_metamodel(
        self, client: httpx.AsyncClient
    ) -> None:
        """No new write path: the cell creates through POST /relationships, rules intact."""
        data = await an_element(client, "data_object", "Invoice")
        process = await an_element(client, "business_process", "Order")

        refused = await client.post(
            "/relationships",
            json={
                "relationship_type": "serving",
                "source_id": data["id"],
                "target_id": process["id"],
            },
        )

        assert refused.status_code == 422
        assert refused.json()["error"] == "illegal_relationship"


@pytest.mark.asyncio
class TestWhoMayRead:
    async def test_no_token_is_a_401_and_a_reader_reads(
        self, service: ArchitectureService, repository: InMemoryRepository, nobody_calling: None
    ) -> None:
        verifier = StaticVerifier({"reader-token": a_reader()})
        app = create_app(
            Settings(debug=True, auth_enabled=True),
            architecture_service=service,
            analysis=repository,
            verifier=verifier,
            packages=an_empty_package_tree(),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            anonymous = await client.post("/matrix", json={"rows": {}, "cols": {}})
            reader = await client.post(
                "/matrix",
                json={"rows": {}, "cols": {}},
                headers={"Authorization": "Bearer reader-token"},
            )

        assert anonymous.status_code == 401
        assert reader.status_code == 200


@pytest.mark.asyncio
class TestTheQualityReport:
    async def test_a_lone_element_is_reported_with_where_to_fix_it(
        self, client: httpx.AsyncClient
    ) -> None:
        node = await an_element(client, "node", "Loner")

        body = (await client.get("/quality")).json()

        orphan = next(f for f in body["findings"] if f["rule"] == "orphan")
        assert orphan["element_id"] == node["id"]
        assert orphan["severity"] == "warning"
        assert orphan["fix_hint"] == f"/elements?element={node['id']}"
        assert {c["rule"] for c in body["counts"]} >= {
            "orphan",
            "naming",
            "inconsistent_relationship",
        }
        assert body["total"] == len(body["findings"])

    async def test_filters_are_applied(self, client: httpx.AsyncClient) -> None:
        await an_element(client, "node", "Loner")

        body = (await client.get("/quality", params={"severity": "info"})).json()

        assert {f["rule"] for f in body["findings"]} == {"no_description"}
        assert body["total"] >= 2  # the counts' total is not narrowed by the filter

    async def test_paging_walks_the_findings(self, client: httpx.AsyncClient) -> None:
        for index in range(3):
            await an_element(client, "node", f"n{index}")

        first = (await client.get("/quality", params={"limit": 4})).json()
        second = (
            await client.get("/quality", params={"limit": 4, "cursor": first["next_cursor"]})
        ).json()

        assert len(first["findings"]) == 4 and first["next_cursor"] == 4
        assert len(second["findings"]) == 2 and second["next_cursor"] is None

    async def test_a_message_naming_an_element_is_data_not_markup(
        self, client: httpx.AsyncClient
    ) -> None:
        await an_element(client, "node", "<script>alert(1)</script>")

        body = (await client.get("/quality")).json()

        assert any(
            "<script>" in f["message"] for f in body["findings"]
        )  # carried verbatim; Vue escapes it

    @pytest.mark.parametrize(
        "params", [{"limit": 0}, {"limit": 1001}, {"cursor": -1}, {"code": "nope"}]
    )
    async def test_out_of_bounds_arguments_are_a_422(
        self, client: httpx.AsyncClient, params: dict[str, Any]
    ) -> None:
        assert (await client.get("/quality", params=params)).status_code == 422

    async def test_no_token_is_a_401(
        self, service: ArchitectureService, repository: InMemoryRepository, nobody_calling: None
    ) -> None:
        app = create_app(
            Settings(debug=True, auth_enabled=True),
            architecture_service=service,
            analysis=repository,
            verifier=StaticVerifier({}),
            packages=an_empty_package_tree(),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as anonymous:
            assert (await anonymous.get("/quality")).status_code == 401


async def an_element_with(
    client: httpx.AsyncClient, element_type: str, name: str, **properties: str
) -> dict[str, Any]:
    response = await client.post(
        "/elements", json={"element_type": element_type, "name": name, "properties": properties}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


@pytest.mark.asyncio
class TestTheAggregates:
    async def test_counting_by_layer_colours_each_bucket_with_a_level(
        self, client: httpx.AsyncClient
    ) -> None:
        await an_element(client, "node", "srv-01")
        await an_element(client, "node", "srv-02")
        await an_element(client, "business_process", "Order")

        body = (await client.get("/analytics/aggregate", params={"by": "layer"})).json()

        assert {b["key"]: b["count"] for b in body["buckets"]} == {"business": 1, "technology": 2}
        assert body["total"] == 3 and body["truncated"] is False
        assert all(0 <= b["level"] <= 4 for b in body["buckets"])
        assert (
            next(b for b in body["buckets"] if b["key"] == "technology")["level"]
            > next(b for b in body["buckets"] if b["key"] == "business")["level"]
        )

    async def test_a_dirty_cost_is_summed_around_and_reported(
        self, client: httpx.AsyncClient
    ) -> None:
        await an_element_with(client, "node", "a", cost="1 200,50")
        await an_element_with(client, "node", "b", cost="300")
        await an_element_with(client, "node", "c", cost="1 200 €")

        body = (
            await client.get(
                "/analytics/aggregate", params={"by": "layer", "metric": "sum", "property": "cost"}
            )
        ).json()

        assert body["buckets"][0]["value"] == 1500.5
        assert body["unreadable"] == 1
        assert body["property_key"] == "cost"

    async def test_a_heat_map_is_a_grid_of_cells_with_ordered_axes(
        self, client: httpx.AsyncClient
    ) -> None:
        await an_element_with(client, "node", "a", lifecycle="retiré")
        await an_element_with(client, "node", "b", lifecycle="planifié")

        body = (
            await client.get(
                "/analytics/heatmap", params={"rows": "layer", "cols": "property:lifecycle"}
            )
        ).json()

        assert body["rows"] == ["technology"]
        assert body["cols"] == ["planifié", "retiré"]
        assert {(c["row"], c["col"], c["count"]) for c in body["cells"]} == {
            ("technology", "planifié", 1),
            ("technology", "retiré", 1),
        }

    async def test_an_empty_model_is_an_empty_answer(self, client: httpx.AsyncClient) -> None:
        body = (await client.get("/analytics/aggregate", params={"by": "layer"})).json()

        assert body["buckets"] == [] and body["total"] == 0

    @pytest.mark.parametrize(
        "params",
        [
            {"by": "bogus"},
            {"by": "property:"},
            {"by": "layer", "metric": "median"},
            {"by": "layer", "metric": "sum", "property": "cost'; --"},
        ],
    )
    async def test_a_malformed_question_is_a_422(
        self, client: httpx.AsyncClient, params: dict[str, str]
    ) -> None:
        assert (await client.get("/analytics/aggregate", params=params)).status_code == 422

    async def test_a_sum_without_a_property_is_a_typed_422(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/analytics/aggregate", params={"by": "layer", "metric": "sum"})

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_analysis"

    async def test_no_token_is_a_401(
        self, service: ArchitectureService, repository: InMemoryRepository, nobody_calling: None
    ) -> None:
        app = create_app(
            Settings(debug=True, auth_enabled=True),
            architecture_service=service,
            analysis=repository,
            verifier=StaticVerifier({}),
            packages=an_empty_package_tree(),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as anonymous:
            assert (
                await anonymous.get("/analytics/aggregate", params={"by": "layer"})
            ).status_code == 401


@pytest.mark.asyncio
class TestScopedByPackageOrQuery:
    async def test_a_scope_names_a_package_or_a_query_never_both(
        self, client: httpx.AsyncClient
    ) -> None:
        both = {"package_id": str(UUID(int=1)), "query_id": str(UUID(int=2))}

        quality = await client.get("/quality", params=both)
        aggregate = await client.get("/analytics/aggregate", params={"by": "layer", **both})
        matrix = await client.post("/matrix", json={"rows": both, "cols": {}})

        assert [r.status_code for r in (quality, aggregate, matrix)] == [422, 422, 422]
        assert quality.json()["error"] == "invalid_analysis"

    async def test_a_package_scope_reaches_the_catalogue(self, client: httpx.AsyncClient) -> None:
        await an_element(client, "node", "srv")

        response = await client.get("/quality", params={"package_id": str(UUID(int=1))})

        # The double does not file elements: the proof is that the door is open, not the filter.
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_the_report_says_whether_a_rule_was_cut_short(client: httpx.AsyncClient) -> None:
    await an_element(client, "node", "Loner")

    body = (await client.get("/quality")).json()

    assert body["partial"] is False
