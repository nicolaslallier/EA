"""`EaClient`: HTTP client to the EA catalogue, idempotent under a retry.

Every request goes through `httpx.MockTransport` — never a socket — per the
autouse guard in `conftest.py`. `EaClient` writes through the EA HTTP API
only (never `/mcp`, never a repository), so a retried Prefect task never
creates the same element or relationship twice.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest

from pipelines.auth import ClientCredentials
from pipelines.ea import EaClient, EaRefused, Metamodel, Written, ea_client
from pipelines.settings import Settings

REQUIRED_SECRETS = {
    "litellm_api_key": "litellm-key",
    "s3_access_key": "s3-access",
    "s3_secret_key": "s3-secret",
    "ea_client_secret": "ea-secret",
}


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> EaClient:
    transport = httpx.MockTransport(handler)
    return EaClient(httpx.Client(transport=transport, base_url="http://ea.test"))


def _element_page(items: list[dict[str, object]], *, total: int) -> httpx.Response:
    return httpx.Response(200, json={"items": items, "total": total, "limit": 200, "offset": 0})


def _an_element(**overrides: object) -> dict[str, object]:
    element: dict[str, object] = {
        "id": str(uuid4()),
        "element_type": "application_component",
        "layer": "application",
        "aspect": "active_structure",
        "name": "Billing",
        "description": "",
        "documentation": "",
        "properties": {},
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
    }
    element.update(overrides)
    return element


def _a_relationship(source_id: UUID, target_id: UUID, **overrides: object) -> dict[str, object]:
    relationship: dict[str, object] = {
        "id": str(uuid4()),
        "relationship_type": "serving",
        "source_id": str(source_id),
        "target_id": str(target_id),
        "source_type": "application_component",
        "target_type": "application_component",
        "name": "",
        "access_type": None,
        "directed": False,
        "properties": {},
        "created_at": "2024-01-01T00:00:00Z",
    }
    relationship.update(overrides)
    return relationship


# --- ea_client ---------------------------------------------------------


def test_ea_client_carries_the_settings_base_url_and_timeout() -> None:
    settings = Settings(
        ea_base_url="http://ea.example:8000", ea_timeout_seconds=7, **REQUIRED_SECRETS
    )  # type: ignore[arg-type]
    client = ea_client(settings)
    assert str(client.http.base_url) == "http://ea.example:8000"
    assert client.http.timeout.connect == 7
    assert isinstance(client.http.auth, ClientCredentials)


# --- metamodel -----------------------------------------------------------


def test_metamodel_returns_the_type_values() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/metamodel"
        return httpx.Response(
            200,
            json={
                "element_types": [
                    {
                        "value": "application_component",
                        "label": "x",
                        "layer": "application",
                        "aspect": "a",
                    }
                ],
                "relationship_types": [
                    {
                        "value": "serving",
                        "category": "structural",
                        "strength": 1,
                        "impact_follows_direction": True,
                    }
                ],
                "layers": ["application"],
            },
        )

    result = _client(handler).metamodel()

    assert result == Metamodel(
        element_types=("application_component",), relationship_types=("serving",)
    )


# --- ensure_element: created ------------------------------------------------


def test_create_returns_created_on_201() -> None:
    element_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/elements"
        body = json.loads(request.content)
        assert body == {
            "element_type": "application_component",
            "name": "Billing",
            "description": "Handles invoices.",
            "properties": {"ingested_from": "runbook.md"},
        }
        return httpx.Response(201, json=_an_element(id=str(element_id), name="Billing"))

    result = _client(handler).ensure_element(
        "application_component", "Billing", "Handles invoices.", "runbook.md"
    )

    assert result == Written(element_id, "created")


# --- ensure_element: 409, exact-name match among near matches ---------------


def test_conflict_finds_billing_and_not_billing_portal() -> None:
    element_id = uuid4()
    calls = {"post": 0, "get": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            calls["post"] += 1
            return httpx.Response(409, json={"error": "duplicate", "detail": "already exists"})
        calls["get"] += 1
        assert request.url.params["search"] == "Billing"
        return _element_page(
            [
                _an_element(id=str(uuid4()), name="Billing Portal", description="x"),
                _an_element(id=str(element_id), name="Billing", description="Handles invoices."),
            ],
            total=2,
        )

    result = _client(handler).ensure_element(
        "application_component", "Billing", "Handles invoices.", "runbook.md"
    )

    assert result == Written(element_id, "existing")
    assert calls == {"post": 1, "get": 1}


# --- ensure_element: 409 + empty stored description -> PATCH ---------------


def test_conflict_with_empty_description_patches_and_returns_completed() -> None:
    element_id = uuid4()
    patched: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(409, json={"error": "duplicate", "detail": "already exists"})
        if request.method == "GET":
            return _element_page(
                [_an_element(id=str(element_id), name="Billing", description="")], total=1
            )
        assert request.method == "PATCH"
        assert request.url.path == f"/elements/{element_id}"
        patched.update(json.loads(request.content))
        return httpx.Response(
            200, json=_an_element(id=str(element_id), description="Handles invoices.")
        )

    result = _client(handler).ensure_element(
        "application_component", "Billing", "Handles invoices.", "runbook.md"
    )

    assert result == Written(element_id, "completed")
    assert patched == {"description": "Handles invoices."}  # never `properties`


# --- ensure_element: 409 + description already filled -> no PATCH ----------


def test_conflict_with_filled_description_does_not_patch_and_returns_existing() -> None:
    element_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(409, json={"error": "duplicate", "detail": "already exists"})
        assert request.method == "GET"
        return _element_page(
            [_an_element(id=str(element_id), name="Billing", description="Already documented.")],
            total=1,
        )

    result = _client(handler).ensure_element(
        "application_component", "Billing", "Handles invoices.", "runbook.md"
    )

    assert result == Written(element_id, "existing")


# --- ensure_element: 409 but not found on any page -> EaRefused ------------


def test_conflict_not_found_after_paging_raises_ea_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(409, json={"error": "duplicate", "detail": "already exists"})
        offset = int(request.url.params["offset"])
        if offset == 0:
            return _element_page([_an_element(name="Billing Portal")], total=201)
        return _element_page([_an_element(name="Billing Portal 2")], total=201)

    with pytest.raises(EaRefused) as excinfo:
        _client(handler).ensure_element(
            "application_component", "Billing", "Handles invoices.", "runbook.md"
        )

    assert excinfo.value.status == 409
    assert excinfo.value.code == "duplicate"


def test_conflict_pages_by_offset_until_the_match_or_the_total_is_reached() -> None:
    element_id = uuid4()
    seen_offsets = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(409, json={"error": "duplicate", "detail": "already exists"})
        offset = int(request.url.params["offset"])
        seen_offsets.append(offset)
        if offset == 0:
            return _element_page([_an_element(name="Billing Portal")], total=201)
        return _element_page(
            [_an_element(id=str(element_id), name="Billing", description="Handles invoices.")],
            total=201,
        )

    result = _client(handler).ensure_element(
        "application_component", "Billing", "Handles invoices.", "runbook.md"
    )

    assert result == Written(element_id, "existing")
    assert seen_offsets == [0, 200]


# --- ensure_relationship: existing on the second page -> no POST -----------


def test_relationship_found_on_second_page_makes_no_post() -> None:
    source_id, target_id = uuid4(), uuid4()
    calls = {"post": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            calls["post"] += 1
            return httpx.Response(201, json=_a_relationship(source_id, target_id))
        offset = int(request.url.params["offset"])
        if offset == 0:
            full_page = [_a_relationship(source_id, uuid4()) for _ in range(200)]
            return httpx.Response(200, json=full_page)
        return httpx.Response(200, json=[_a_relationship(source_id, target_id)])

    result = _client(handler).ensure_relationship("serving", source_id, target_id)

    assert result == "existing"
    assert calls["post"] == 0


# --- ensure_relationship: absent -> POST -------------------------------


def test_relationship_absent_posts_and_returns_created() -> None:
    source_id, target_id = uuid4(), uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[])
        assert request.method == "POST"
        assert request.url.path == "/relationships"
        body = json.loads(request.content)
        assert body == {
            "relationship_type": "serving",
            "source_id": str(source_id),
            "target_id": str(target_id),
        }
        return httpx.Response(201, json=_a_relationship(source_id, target_id))

    result = _client(handler).ensure_relationship("serving", source_id, target_id)

    assert result == "created"


# --- 422 illegal_relationship -> EaRefused ---------------------------------


def test_422_illegal_relationship_raises_ea_refused() -> None:
    source_id, target_id = uuid4(), uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(
            422,
            json={"error": "illegal_relationship", "detail": "not permitted between these types"},
        )

    with pytest.raises(EaRefused) as excinfo:
        _client(handler).ensure_relationship("serving", source_id, target_id)

    assert excinfo.value.status == 422
    assert excinfo.value.code == "illegal_relationship"


# --- 500 -> HTTPStatusError, left for Prefect to retry ----------------------


def test_500_on_ensure_element_raises_http_status_error_not_ea_refused() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    with pytest.raises(httpx.HTTPStatusError):
        _client(handler).ensure_element(
            "application_component", "Billing", "Handles invoices.", "runbook.md"
        )


def test_500_on_metamodel_raises_http_status_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    with pytest.raises(httpx.HTTPStatusError):
        _client(handler).metamodel()


def test_500_on_ensure_relationship_raises_http_status_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    with pytest.raises(httpx.HTTPStatusError):
        _client(handler).ensure_relationship("serving", uuid4(), uuid4())


# --- an unreadable error body still becomes an EaRefused --------------------


def test_unreadable_error_body_becomes_http_status_code_named_refusal() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not json")

    with pytest.raises(EaRefused) as excinfo:
        _client(handler).metamodel()

    assert excinfo.value.status == 404
    assert excinfo.value.code == "http_404"
