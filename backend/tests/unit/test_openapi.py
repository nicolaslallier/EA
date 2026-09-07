"""The OpenAPI document the frontend client is generated from.

The document is a committed artefact: `frontend/src/api/` is regenerated from
it and CI compares the result with what is in the tree. So it has to be
buildable without a database, and it has to be the same document on every
machine — hence the assertion about the environment below.
"""

from __future__ import annotations

import json

import pytest

from ea.openapi import main, openapi_document


def test_the_document_is_built_without_touching_the_database() -> None:
    """No Neo4j password, no driver, no network: the schema is static data."""
    document = openapi_document()

    assert str(document["openapi"]).startswith("3.")
    assert document["info"]["title"] == "EA API"


def test_every_element_endpoint_is_described() -> None:
    paths = openapi_document()["paths"]

    assert set(paths["/elements"]) == {"get", "post"}
    assert set(paths["/elements/{element_id}"]) == {"get", "patch", "delete"}


def test_the_element_schemas_the_client_needs_are_named() -> None:
    schemas = openapi_document()["components"]["schemas"]

    assert {"ElementCreate", "ElementUpdate", "ElementRead", "ElementPage"} <= set(schemas)


def test_the_environment_cannot_change_the_generated_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A developer's local settings must not produce a diff nobody else can reproduce."""
    monkeypatch.setenv("EA_APP_NAME", "Nicolas' laptop")

    assert openapi_document()["info"]["title"] == "EA API"


def test_the_module_writes_the_document_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """`make openapi` redirects stdout into the committed file, so it has to be JSON alone."""
    main()

    written = json.loads(capsys.readouterr().out)
    assert written == openapi_document()
