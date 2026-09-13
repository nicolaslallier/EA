"""One validated call to the LLM gateway.

The gateway is LiteLLM, reached over its OpenAI-shaped `/v1/chat/completions`
— the code never names a real model, only the `Alias` the deployment's
`litellm.yaml` maps to one (see `pipelines/litellm.yaml`). `extract` is a
single `httpx` POST with no retry of its own: LiteLLM already retries the
provider (`num_retries: 2`), and a malformed answer is not a transient
failure worth retrying — it is a prompt or a schema to fix.

`strict_schema` turns a Pydantic v2 JSON schema into one an OpenAI-shaped
`json_schema` response format accepts as `strict: true`: every object closed
(`additionalProperties: false`, every property required) and, on request, a
field's own list of allowed values folded in as `enum` — wherever that field
appears, including inside a `$defs` entry reached only through a list.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ValidationError

from pipelines.settings import Settings

Alias = Literal["smart", "fast"]

#: Relative to the base URL the caller's client already carries.
_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"

#: The whole instruction the model gets beyond the schema itself — the schema
#: says what shape is acceptable, this says there is nothing else to say.
_SYSTEM_PROMPT = (
    "Respond with a single JSON object that matches the given JSON schema exactly. "
    "Output only that JSON object: no explanation, no markdown fences, no extra text."
)


class ExtractionFailed(Exception):  # noqa: N818 -- exact name from the task interface
    """The gateway's answer could not be turned into the requested schema.

    Covers a truncated answer, an empty one, invalid JSON, a JSON document
    that does not match the schema, and a value outside a declared `enum`
    that Pydantic's own types would not have caught (`enums` targets plain
    `str` fields, not `Literal`s — the metamodel's allowed values are only
    known at run time, from `/metamodel`). The message never carries the raw
    content: it may be long, and it is not this exception's job to log it.
    """


def strict_schema(schema: type[BaseModel], enums: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    """`schema.model_json_schema()`, tightened for an OpenAI `strict` response format.

    Every object — the root and each entry of `$defs` — gets
    `additionalProperties: false` and `required` set to all of its own
    properties; every `default` is stripped, since a strict schema demands the
    field regardless. `enums` maps a field *name* to its allowed values,
    injected as `enum` on every property of that name, at any depth — a `$ref`
    is never inlined to do this, its target in `$defs` is edited directly, so
    a field reached only through a list of a referenced model is covered too.
    """
    document = schema.model_json_schema()
    _tighten(document, enums)
    return document


def _tighten(node: Any, enums: Mapping[str, Sequence[str]]) -> None:
    if isinstance(node, list):
        for item in node:
            _tighten(item, enums)
        return
    if not isinstance(node, dict):
        return
    node.pop("default", None)
    properties = node.get("properties")
    if isinstance(properties, dict):
        node["additionalProperties"] = False
        node["required"] = list(properties.keys())
        for name, property_schema in properties.items():
            if name in enums:
                _inject_enum(property_schema, list(enums[name]))
    for value in node.values():
        _tighten(value, enums)


def _inject_enum(property_schema: Any, values: list[str]) -> None:
    """Add `enum` to a property schema, following `anyOf` to its real branch.

    `Optional[str]` becomes `{"anyOf": [{"type": "string"}, {"type": "null"}]}`
    — the `enum` belongs on the string branch, not the null one.
    """
    if not isinstance(property_schema, dict):
        return
    if "anyOf" in property_schema:
        for branch in property_schema["anyOf"]:
            _inject_enum(branch, values)
        return
    if property_schema.get("type") == "null":
        return
    property_schema["enum"] = values


def extract[T: BaseModel](
    http: httpx.Client,
    prompt: str,
    schema: type[T],
    *,
    model: Alias = "smart",
    enums: Mapping[str, Sequence[str]] | None = None,
) -> T:
    """Ask the gateway for JSON matching `schema`, and validate what comes back.

    One POST on `http` (its base URL and auth are the caller's concern — see
    `llm_client`), no `temperature`, no retry. An HTTP error status is raised
    as `httpx.HTTPStatusError` and not swallowed; every other way the answer
    can be unusable — truncated, empty, invalid JSON, schema mismatch, or a
    value outside `enums` — raises `ExtractionFailed`.
    """
    enums = enums or {}
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "schema": strict_schema(schema, enums),
                "strict": True,
            },
        },
    }
    response = http.post(_CHAT_COMPLETIONS_PATH, json=body)
    response.raise_for_status()
    choice = response.json()["choices"][0]
    if choice.get("finish_reason") == "length":
        raise ExtractionFailed(f"the gateway truncated its answer for {schema.__name__}")
    content = choice["message"]["content"]
    if not content:
        raise ExtractionFailed(f"the gateway returned an empty answer for {schema.__name__}")
    try:
        result = schema.model_validate_json(content)
    except ValidationError as error:
        raise ExtractionFailed(
            f"the gateway's answer does not match {schema.__name__}: {error}"
        ) from error
    _check_enums(result, enums, schema.__name__)
    return result


def _check_enums(value: Any, enums: Mapping[str, Sequence[str]], schema_name: str) -> None:
    """Refuse a value outside its declared `enum` that a plain `str` field let through.

    `strict_schema` asked the gateway for these values, but the gateway is not
    trusted to have honoured it — walked over every nested model and list, the
    same shape `_tighten` builds the schema from.
    """
    if isinstance(value, BaseModel):
        for name, field_value in value:
            if name in enums and field_value is not None and field_value not in enums[name]:
                msg = f"{schema_name}.{name} is {field_value!r}, not one of the allowed values"
                raise ExtractionFailed(msg)
            _check_enums(field_value, enums, schema_name)
    elif isinstance(value, list | tuple):
        for item in value:
            _check_enums(item, enums, schema_name)


def llm_client(settings: Settings) -> httpx.Client:
    """One pooled client for the gateway, carrying its base URL, key and timeout."""
    return httpx.Client(
        base_url=settings.litellm_base_url,
        headers={"Authorization": f"Bearer {settings.litellm_api_key.get_secret_value()}"},
        timeout=settings.llm_timeout_seconds,
    )
