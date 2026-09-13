"""`llm.py`: a schema-validated call to the LLM gateway.

Every request goes through `httpx.MockTransport` — never a socket — per the
autouse guard in `conftest.py`. The Pydantic models below carry a nested
object *and* a list of that object, because `strict_schema` has to reach a
`$defs` entry referenced from a list, not just the root.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import BaseModel

from pipelines.llm import ExtractionFailed, extract, llm_client, strict_schema
from pipelines.settings import Settings

ELEMENT_TYPES = ("application_component", "node")


class Child(BaseModel):
    element_type: str
    label: str


class Parent(BaseModel):
    name: str
    element_type: str
    children: list[Child]


ENUMS = {"element_type": ELEMENT_TYPES}


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://gateway.test")


def _content_response(content: str, *, finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {"finish_reason": finish_reason, "message": {"content": content}},
            ]
        },
    )


# --- strict_schema -----------------------------------------------------


def test_every_object_gets_additional_properties_false_and_full_required() -> None:
    schema = strict_schema(Parent, {})
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"name", "element_type", "children"}
    child_schema = schema["$defs"]["Child"]
    assert child_schema["additionalProperties"] is False
    assert set(child_schema["required"]) == {"element_type", "label"}


def test_default_is_stripped_everywhere() -> None:
    class WithDefault(BaseModel):
        note: str = "unset"

    schema = strict_schema(WithDefault, {})
    assert "default" not in schema["properties"]["note"]
    assert "default" not in json.dumps(schema).replace('"default"', "")  # sanity: no leftovers


def test_enum_is_injected_on_the_root_property() -> None:
    schema = strict_schema(Parent, ENUMS)
    assert schema["properties"]["element_type"]["enum"] == list(ELEMENT_TYPES)


def test_enum_reaches_a_field_inside_a_defs_object_referenced_from_a_list() -> None:
    schema = strict_schema(Parent, ENUMS)
    child_schema = schema["$defs"]["Child"]
    assert child_schema["properties"]["element_type"]["enum"] == list(ELEMENT_TYPES)
    # A field with the same name elsewhere (`label`) is untouched.
    assert "enum" not in child_schema["properties"]["label"]


def test_ref_stays_a_ref_rather_than_being_inlined() -> None:
    schema = strict_schema(Parent, ENUMS)
    children_schema = schema["properties"]["children"]
    assert children_schema["items"] == {"$ref": "#/$defs/Child"}


def test_enum_lands_on_the_string_branch_of_an_optional_field_not_the_null_branch() -> None:
    class WithOptionalEnum(BaseModel):
        element_type: str | None = None

    schema = strict_schema(WithOptionalEnum, ENUMS)
    branches = schema["properties"]["element_type"]["anyOf"]
    string_branch = next(branch for branch in branches if branch.get("type") == "string")
    null_branch = next(branch for branch in branches if branch.get("type") == "null")
    assert string_branch["enum"] == list(ELEMENT_TYPES)
    assert "enum" not in null_branch


# --- extract: the request body ------------------------------------------


def test_the_request_body_names_the_alias_and_is_strict_with_no_temperature() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return _content_response(
            json.dumps(
                {
                    "name": "n",
                    "element_type": "node",
                    "children": [{"element_type": "application_component", "label": "c"}],
                }
            )
        )

    extract(_client(handler), "describe it", Parent, model="fast", enums=ENUMS)

    assert captured["model"] == "fast"
    assert "temperature" not in captured
    json_schema = captured["response_format"]["json_schema"]  # type: ignore[index]
    assert json_schema["name"] == "Parent"
    assert json_schema["strict"] is True
    assert json_schema["schema"]["properties"]["element_type"]["enum"] == list(ELEMENT_TYPES)
    messages = captured["messages"]
    assert messages[0]["role"] == "system"  # type: ignore[index]
    assert messages[1] == {"role": "user", "content": "describe it"}  # type: ignore[index]


# --- extract: success -----------------------------------------------------


def test_extract_returns_the_validated_model() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _content_response(
            json.dumps(
                {
                    "name": "n",
                    "element_type": "node",
                    "children": [{"element_type": "application_component", "label": "c"}],
                }
            )
        )

    result = extract(_client(handler), "describe it", Parent, enums=ENUMS)

    assert isinstance(result, Parent)
    assert result.name == "n"
    assert result.children[0].label == "c"


def test_extract_defaults_to_the_smart_alias() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return _content_response(json.dumps({"name": "n", "element_type": "node", "children": []}))

    extract(_client(handler), "describe it", Parent)

    assert captured["model"] == "smart"


# --- extract: failure modes ------------------------------------------------


def test_no_choices_key_raises_extraction_failed() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    with pytest.raises(ExtractionFailed):
        extract(_client(handler), "describe it", Parent, enums=ENUMS)


def test_empty_choices_list_raises_extraction_failed() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    with pytest.raises(ExtractionFailed):
        extract(_client(handler), "describe it", Parent, enums=ENUMS)


def test_choice_without_message_raises_extraction_failed() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop"}]})

    with pytest.raises(ExtractionFailed):
        extract(_client(handler), "describe it", Parent, enums=ENUMS)


def test_a_choice_that_is_not_an_object_raises_extraction_failed() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": ["x"]})

    with pytest.raises(ExtractionFailed):
        extract(_client(handler), "describe it", Parent, enums=ENUMS)


def test_non_json_body_raises_extraction_failed() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json at all")

    with pytest.raises(ExtractionFailed):
        extract(_client(handler), "describe it", Parent, enums=ENUMS)


def test_finish_reason_length_raises_extraction_failed() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _content_response("{}", finish_reason="length")

    with pytest.raises(ExtractionFailed):
        extract(_client(handler), "describe it", Parent, enums=ENUMS)


def test_empty_content_raises_extraction_failed() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _content_response("")

    with pytest.raises(ExtractionFailed):
        extract(_client(handler), "describe it", Parent, enums=ENUMS)


def test_invalid_json_content_raises_extraction_failed() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _content_response("{not json")

    with pytest.raises(ExtractionFailed):
        extract(_client(handler), "describe it", Parent, enums=ENUMS)


def test_schema_mismatch_raises_extraction_failed() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _content_response(json.dumps({"name": "n"}))  # missing required fields

    with pytest.raises(ExtractionFailed):
        extract(_client(handler), "describe it", Parent, enums=ENUMS)


def test_value_outside_enum_raises_extraction_failed_even_though_pydantic_accepted_it() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _content_response(
            json.dumps({"name": "n", "element_type": "not-a-real-type", "children": []})
        )

    with pytest.raises(ExtractionFailed):
        extract(_client(handler), "describe it", Parent, enums=ENUMS)


def test_value_outside_enum_in_a_nested_child_is_also_caught() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _content_response(
            json.dumps(
                {
                    "name": "n",
                    "element_type": "node",
                    "children": [{"element_type": "not-a-real-type", "label": "c"}],
                }
            )
        )

    with pytest.raises(ExtractionFailed):
        extract(_client(handler), "describe it", Parent, enums=ENUMS)


def test_http_error_status_is_not_swallowed() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(httpx.HTTPStatusError):
        extract(_client(handler), "describe it", Parent, enums=ENUMS)


# --- llm_client -------------------------------------------------------


def test_llm_client_carries_the_base_url_bearer_token_and_timeout() -> None:
    settings = Settings(
        litellm_base_url="http://litellm.test:4000",
        litellm_api_key="s3cr3t",  # type: ignore[arg-type]
        llm_timeout_seconds=12,
        s3_access_key="a",  # type: ignore[arg-type]
        s3_secret_key="b",  # type: ignore[arg-type]
        ea_client_secret="ea-secret",  # type: ignore[arg-type]
    )

    client = llm_client(settings)

    assert str(client.base_url) == "http://litellm.test:4000"
    assert client.headers["authorization"] == "Bearer s3cr3t"
    assert client.timeout.read == 12
