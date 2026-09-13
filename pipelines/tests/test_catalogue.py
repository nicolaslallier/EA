"""`alimenter-catalogue`: files from S3, through the LLM, into the EA catalogue.

The flow runs for real under `prefect_test_harness` (a temporary server on
loopback); everything it talks to is a double handed in through `clients()`,
the module's only seam: a fake EA API and a fake LLM gateway behind
`httpx.MockTransport`, and a fake S3 object.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx
import pytest
from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import ArtifactFilter, ArtifactFilterFlowRunId
from prefect.settings import PREFECT_LOCAL_STORAGE_PATH, temporary_settings
from prefect.states import State
from prefect.testing.utilities import prefect_test_harness

from pipelines import catalogue
from pipelines.ea import EaClient
from test_storage import FakeS3

ELEMENT_TYPES = ["application_component", "node", "business_process"]
RELATIONSHIP_TYPES = ["serving", "assignment", "realization"]
BUCKET = "catalogue-test"

EXTRACTIONS: dict[str, dict[str, Any]] = {
    "DOC:a": {
        "elements": [
            {
                "element_type": "application_component",
                "name": "Billing",
                "description": "Handles invoices.",
            },
            {"element_type": "node", "name": "Server", "description": ""},
        ],
        "relationships": [
            {
                "relationship_type": "serving",
                "source_name": "Server",
                "source_type": "node",
                "target_name": "Billing",
                "target_type": "application_component",
            },
        ],
    },
    "DOC:b": {
        "elements": [
            {"element_type": "application_component", "name": "Billing", "description": ""},
            {"element_type": "application_component", "name": "CRM", "description": "Customers."},
        ],
        "relationships": [
            {
                "relationship_type": "serving",
                "source_name": "CRM",
                "source_type": "application_component",
                "target_name": "Billing",
                "target_type": "application_component",
            },
        ],
    },
    "DOC:illegal": {
        "elements": [
            {"element_type": "application_component", "name": "Billing", "description": ""},
            {"element_type": "node", "name": "Server", "description": ""},
        ],
        "relationships": [
            {
                "relationship_type": "assignment",
                "source_name": "Billing",
                "source_type": "application_component",
                "target_name": "Server",
                "target_type": "node",
            },
        ],
    },
    "DOC:refused-element": {
        "elements": [
            {"element_type": "business_process", "name": "Invoicing", "description": ""},
            {"element_type": "application_component", "name": "Billing", "description": ""},
        ],
        "relationships": [
            {
                "relationship_type": "serving",
                "source_name": "Billing",
                "source_type": "application_component",
                "target_name": "Invoicing",
                "target_type": "business_process",
            },
        ],
    },
    "DOC:ghost": {
        "elements": [
            {"element_type": "application_component", "name": "Billing", "description": ""},
        ],
        "relationships": [
            {
                "relationship_type": "serving",
                "source_name": "Ghost",
                "source_type": "node",
                "target_name": "Billing",
                "target_type": "application_component",
            },
        ],
    },
}


@dataclass
class FakeEa:
    """Just enough of the EA API: 409 on `(type, name)`, 422 on an `assignment`
    link and on a `business_process` element."""

    elements: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    #: Once this many elements exist, every further `POST /elements` answers 500.
    down_after: int | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if (
            path == "/elements"
            and method == "POST"
            and self.down_after is not None
            and len(self.elements) >= self.down_after
        ):
            return httpx.Response(500, json={"error": "internal_error", "detail": "down"})
        if path == "/metamodel":
            return httpx.Response(
                200,
                json={
                    "element_types": [{"value": value} for value in ELEMENT_TYPES],
                    "relationship_types": [{"value": value} for value in RELATIONSHIP_TYPES],
                },
            )
        if path == "/elements" and method == "POST":
            body = json.loads(request.content)
            identity = (body["element_type"], body["name"])
            if body["element_type"] == "business_process":
                return httpx.Response(422, json={"error": "invalid_element", "detail": "no"})
            if identity in self.elements:
                return httpx.Response(409, json={"error": "duplicate", "detail": "exists"})
            self.elements[identity] = {**body, "id": str(uuid4())}
            return httpx.Response(201, json=self.elements[identity])
        if path == "/elements":
            params = request.url.params
            items = [
                element
                for element in self.elements.values()
                if element["element_type"] == params["element_type"]
                and params["search"].lower() in element["name"].lower()
            ]
            return httpx.Response(200, json={"items": items, "total": len(items)})
        if path.startswith("/elements/") and method == "PATCH":
            element = next(e for e in self.elements.values() if path.endswith(e["id"]))
            element.update(json.loads(request.content))
            return httpx.Response(200, json=element)
        if path == "/relationships" and method == "GET":
            params = request.url.params
            return httpx.Response(
                200,
                json=[
                    link
                    for link in self.relationships
                    if link["source_id"] == params["element_id"]
                    and link["relationship_type"] == params["relationship_type"]
                ],
            )
        if path == "/relationships":
            body = json.loads(request.content)
            if body["relationship_type"] == "assignment":
                return httpx.Response(
                    422, json={"error": "invalid_relationship", "detail": "not permitted"}
                )
            self.relationships.append(body)
            return httpx.Response(201, json={**body, "id": str(uuid4())})
        raise AssertionError(f"unexpected {method} {path}")


@dataclass
class FakeLlm:
    """Answers with the extraction whose marker the prompt carries; garbage otherwise."""

    bodies: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        prompt = body["messages"][-1]["content"]
        answer = next(
            (json.dumps(value) for marker, value in EXTRACTIONS.items() if marker in prompt),
            "this is not JSON",
        )
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": answer}}]}
        )


@dataclass
class World:
    ea: FakeEa
    llm: FakeLlm
    s3: FakeS3

    def run(self) -> State[Any]:
        return catalogue.alimenter_catalogue(prefix="inbox/", return_state=True)  # type: ignore[call-overload,no-any-return]


@pytest.fixture(scope="session")
def harness(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """One temporary Prefect server for the whole session, with its analytics off."""
    # A failed run persists its exception as a result: under a temporary
    # directory, not in the developer's `~/.prefect/storage`.
    storage = tmp_path_factory.mktemp("prefect-storage")
    with (
        pytest.MonkeyPatch.context() as patch,
        temporary_settings(updates={PREFECT_LOCAL_STORAGE_PATH: storage}),
    ):
        # Both halves of Prefect's analytics would leave this machine: the
        # client honours DO_NOT_TRACK, the server subprocess its own setting.
        patch.setenv("DO_NOT_TRACK", "1")
        patch.setenv("PREFECT_SERVER_ANALYTICS_ENABLED", "false")
        with prefect_test_harness():
            yield


@pytest.fixture
def world(harness: None, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("PIPELINES_LITELLM_API_KEY", "litellm-key")
    monkeypatch.setenv("PIPELINES_S3_ACCESS_KEY", "s3-access")
    monkeypatch.setenv("PIPELINES_S3_SECRET_KEY", "s3-secret")
    monkeypatch.setenv("PIPELINES_S3_BUCKET", BUCKET)
    world = World(FakeEa(), FakeLlm(), FakeS3({}))

    def clients() -> tuple[EaClient, httpx.Client, FakeS3]:
        return (
            EaClient(httpx.Client(transport=httpx.MockTransport(world.ea), base_url="http://ea")),
            httpx.Client(transport=httpx.MockTransport(world.llm), base_url="http://llm"),
            world.s3,
        )

    monkeypatch.setattr(catalogue, "clients", clients)
    return world


def rows(state: State[Any]) -> list[dict[str, str]]:
    """The table artifact the run recorded."""
    with get_client(sync_client=True) as client:
        artifacts = client.read_artifacts(
            artifact_filter=ArtifactFilter(
                flow_run_id=ArtifactFilterFlowRunId(any_=[state.state_details.flow_run_id])
            )
        )
    (artifact,) = [a for a in artifacts if a.key == "alimenter-catalogue"]
    data = artifact.data
    return json.loads(data) if isinstance(data, str) else data  # type: ignore[no-any-return]


def outcomes(state: State[Any]) -> set[tuple[str, str, str]]:
    return {(row["key"], row["subject"], row["outcome"]) for row in rows(state)}


def test_two_files_write_each_element_once(world: World) -> None:
    world.s3.objects.update({"inbox/b.md": b"DOC:b", "inbox/a.md": b"DOC:a"})

    state = world.run()

    assert state.is_completed()
    assert sorted(name for _, name in world.ea.elements) == ["Billing", "CRM", "Server"]
    assert world.ea.elements["application_component", "Billing"]["description"] == (
        "Handles invoices."
    )
    assert world.ea.elements["node", "Server"]["properties"] == {
        "ingested_from": f"s3://{BUCKET}/inbox/a.md"
    }
    assert len(world.ea.relationships) == 2
    assert outcomes(state) == {
        ("inbox/a.md", "application_component Billing", "created"),
        ("inbox/a.md", "node Server", "created"),
        ("inbox/a.md", "Server serving Billing", "created"),
        ("inbox/b.md", "application_component Billing", "existing"),
        ("inbox/b.md", "application_component CRM", "created"),
        ("inbox/b.md", "CRM serving Billing", "created"),
    }


def test_a_description_missing_from_the_catalogue_is_completed(world: World) -> None:
    world.s3.objects.update({"inbox/1.md": b"DOC:b", "inbox/2.md": b"DOC:a"})

    state = world.run()

    assert ("inbox/2.md", "application_component Billing", "completed") in outcomes(state)


def test_an_illegal_link_is_recorded_and_the_run_completes(world: World) -> None:
    world.s3.objects["inbox/illegal.md"] = b"DOC:illegal"

    state = world.run()

    assert state.is_completed()
    assert world.ea.relationships == []
    (refusal,) = [row for row in rows(state) if row["subject"] == "Billing assignment Server"]
    assert refusal["outcome"] == "invalid_relationship"
    assert refusal["detail"] == "not permitted"


def test_a_refused_element_is_recorded_and_its_links_cannot_resolve(world: World) -> None:
    world.s3.objects["inbox/refused.md"] = b"DOC:refused-element"

    state = world.run()

    assert state.is_completed()
    assert list(world.ea.elements) == [("application_component", "Billing")]
    assert outcomes(state) == {
        ("inbox/refused.md", "business_process Invoicing", "invalid_element"),
        ("inbox/refused.md", "application_component Billing", "created"),
        ("inbox/refused.md", "Billing serving Invoicing", "unknown_endpoint"),
    }


def test_an_unknown_endpoint_is_recorded(world: World) -> None:
    world.s3.objects["inbox/ghost.md"] = b"DOC:ghost"

    state = world.run()

    assert state.is_completed()
    assert world.ea.relationships == []
    assert ("inbox/ghost.md", "Ghost serving Billing", "unknown_endpoint") in outcomes(state)


def test_a_failed_file_does_not_stop_the_others_and_fails_the_run(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PIPELINES_MAX_SOURCE_CHARS", "50")
    world.s3.objects.update(
        {
            "inbox/1-latin1.md": "DOC:a été".encode("latin-1"),
            "inbox/2-garbage.md": b"DOC:nothing-the-llm-knows",
            "inbox/3-long.md": b"DOC:a " + b"x" * 50,
            "inbox/4-good.md": b"DOC:b",
        }
    )

    state = world.run()

    assert state.is_failed()
    assert sorted(name for _, name in world.ea.elements) == ["Billing", "CRM"]
    failed = {row["key"] for row in rows(state) if row["outcome"] == "extraction_failed"}
    assert failed == {"inbox/1-latin1.md", "inbox/2-garbage.md", "inbox/3-long.md"}
    # Neither the undecodable file nor the oversized one ever reaches the LLM.
    assert len(world.llm.bodies) == 2


def test_a_write_that_exhausts_its_retries_still_records_what_was_written(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No retry delay in a test: the retry policy is not what is under test here.
    monkeypatch.setattr(catalogue, "write_element", catalogue.write_element.with_options(retries=0))
    world.ea.down_after = 1
    world.s3.objects["inbox/a.md"] = b"DOC:a"

    state = world.run()

    assert state.is_failed()
    assert list(world.ea.elements) == [("application_component", "Billing")]
    assert outcomes(state) == {("inbox/a.md", "application_component Billing", "created")}


def test_a_second_run_creates_nothing(world: World) -> None:
    world.s3.objects.update({"inbox/a.md": b"DOC:a", "inbox/b.md": b"DOC:b"})
    world.run()
    elements, relationships = dict(world.ea.elements), list(world.ea.relationships)

    state = world.run()

    assert state.is_completed()
    assert world.ea.elements == elements
    assert world.ea.relationships == relationships
    assert {row["outcome"] for row in rows(state)} == {"existing"}


def test_the_enums_sent_to_the_llm_are_the_metamodel_lists(world: World) -> None:
    world.s3.objects["inbox/a.md"] = b"DOC:a"

    world.run()

    (body,) = world.llm.bodies
    assert body["model"] == "smart"
    definitions = body["response_format"]["json_schema"]["schema"]["$defs"]
    assert definitions["ExtractedElement"]["properties"]["element_type"]["enum"] == ELEMENT_TYPES
    relationship = definitions["ExtractedRelationship"]["properties"]
    assert relationship["relationship_type"]["enum"] == RELATIONSHIP_TYPES
    assert relationship["source_type"]["enum"] == ELEMENT_TYPES
    assert relationship["target_type"]["enum"] == ELEMENT_TYPES


def test_clients_are_built_from_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "PIPELINES_EA_BASE_URL": "http://ea.example:8000",
        "PIPELINES_LITELLM_BASE_URL": "http://llm.example:4000",
        "PIPELINES_LITELLM_API_KEY": "litellm-key",
        "PIPELINES_S3_ENDPOINT": "minio.example:9000",
        "PIPELINES_S3_ACCESS_KEY": "s3-access",
        "PIPELINES_S3_SECRET_KEY": "s3-secret",
    }.items():
        monkeypatch.setenv(name, value)

    ea, llm, s3 = catalogue.clients()

    assert str(ea.http.base_url) == "http://ea.example:8000"
    assert str(llm.base_url) == "http://llm.example:4000"
    assert s3._base_url.host == "minio.example:9000"
