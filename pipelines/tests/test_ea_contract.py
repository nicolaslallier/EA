"""`EaClient` stays inside what `backend/openapi.json` actually promises.

Every (method, path) `EaClient` calls, and every field it reads or sends, is
checked against the committed OpenAPI schema — the single source of truth for
the front/back contract (CLAUDE.md). Read from the file rather than repeating
the shapes from memory: it is what `make openapi` writes off the real routes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OPENAPI: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[2] / "backend" / "openapi.json").read_text(encoding="utf-8")
)


def _operation(method: str, path: str) -> dict[str, Any]:
    operation = OPENAPI["paths"][path][method]
    assert isinstance(operation, dict)
    return operation


def _param_names(operation: dict[str, Any]) -> set[str]:
    return {param["name"] for param in operation.get("parameters", [])}


def _schema(name: str) -> dict[str, Any]:
    schema = OPENAPI["components"]["schemas"][name]
    assert isinstance(schema, dict)
    return schema


def _ref_name(ref: dict[str, Any]) -> str:
    return str(ref["$ref"]).rsplit("/", 1)[-1]


def _request_body_schema(operation: dict[str, Any]) -> dict[str, Any]:
    ref = operation["requestBody"]["content"]["application/json"]["schema"]
    return _schema(_ref_name(ref))


def _response_schema(operation: dict[str, Any], status: str = "200") -> dict[str, Any]:
    body = operation["responses"][status]["content"]["application/json"]["schema"]
    if "$ref" in body:
        return _schema(_ref_name(body))
    assert body["type"] == "array"
    return _schema(_ref_name(body["items"]))


# --- GET /metamodel ---------------------------------------------------------


def test_metamodel_path_and_fields_exist() -> None:
    body = _response_schema(_operation("get", "/metamodel"))
    assert {"element_types", "relationship_types"} <= set(body["properties"])
    assert "value" in _schema("ElementTypeRead")["properties"]
    assert "value" in _schema("RelationshipTypeRead")["properties"]


# --- POST /elements ---------------------------------------------------------


def test_create_element_fields_and_success_status_exist() -> None:
    operation = _operation("post", "/elements")
    assert "201" in operation["responses"]
    body = _request_body_schema(operation)
    assert {"element_type", "name", "description", "properties"} <= set(body["properties"])
    created = _response_schema(operation, "201")
    assert "id" in created["properties"]


def test_create_element_declares_a_409_response() -> None:
    assert "409" in _operation("post", "/elements")["responses"]


# --- GET /elements (search after a 409) -------------------------------------


def test_list_elements_path_and_fields_exist() -> None:
    operation = _operation("get", "/elements")
    assert {"element_type", "search", "limit", "offset"} <= _param_names(operation)
    page = _response_schema(operation)
    assert {"items", "total"} <= set(page["properties"])
    item = _schema("ElementRead")
    assert {"id", "name", "description"} <= set(item["properties"])


# --- PATCH /elements/{element_id} -------------------------------------------


def test_update_element_description_field_exists() -> None:
    body = _request_body_schema(_operation("patch", "/elements/{element_id}"))
    assert "description" in body["properties"]
    # `properties` also exists on ElementUpdate, but EaClient never sends it in
    # a PATCH — it replaces the whole map (domain/model.py), never merges.
    assert "properties" in body["properties"]


# --- GET /relationships ------------------------------------------------------


def test_list_relationships_path_and_fields_exist() -> None:
    operation = _operation("get", "/relationships")
    assert {"element_id", "relationship_type", "limit", "offset"} <= _param_names(operation)
    item = _schema("RelationshipRead")
    assert {"source_id", "target_id"} <= set(item["properties"])


# --- POST /relationships ------------------------------------------------------


def test_create_relationship_fields_and_statuses_exist() -> None:
    operation = _operation("post", "/relationships")
    assert "201" in operation["responses"]
    assert "422" in operation["responses"]
    body = _request_body_schema(operation)
    assert {"relationship_type", "source_id", "target_id"} <= set(body["properties"])


# --- The 4xx envelope EaRefused is built from -------------------------------


def test_error_response_has_error_and_detail() -> None:
    assert set(_schema("ErrorResponse")["properties"]) == {"error", "detail"}


# --- The limit EaClient pages with is the API's own maximum ------------------


def test_elements_limit_maximum_is_200() -> None:
    limit_param = next(
        param for param in _operation("get", "/elements")["parameters"] if param["name"] == "limit"
    )
    assert limit_param["schema"]["maximum"] == 200


def test_relationships_limit_maximum_is_200() -> None:
    limit_param = next(
        param
        for param in _operation("get", "/relationships")["parameters"]
        if param["name"] == "limit"
    )
    assert limit_param["schema"]["maximum"] == 200
