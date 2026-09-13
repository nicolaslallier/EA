"""`EaClient`: the pipeline's only door into the EA catalogue.

HTTP, and only HTTP — never `/mcp`, never a repository (see
`docs/adr/0027` and `CLAUDE.md`'s global constraint on this). A Prefect task
that writes here is retried (`retries=2`), so every write is idempotent: a
second `ensure_element` for the same type and name never creates a second
element, and a second `ensure_relationship` never creates a second link. The
client restates no ArchiMate rule — `metamodel()` reads the types the API
allows at the moment they are needed, exactly as the SPA does.

Bounds (the query limit, the shape of the error envelope) are read off
`backend/src/ea/api/schemas.py` and `backend/src/ea/api/errors.py` rather
than repeated from memory; `tests/test_ea_contract.py` checks every path,
parameter and field this module uses still exists in `backend/openapi.json`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NoReturn
from uuid import UUID

import httpx

from pipelines.settings import Settings

#: `api/schemas.py`: `Limit = Annotated[int, Field(ge=1, le=200)]` — the API's
#: own maximum, shared by `GET /elements` and `GET /relationships`.
_PAGE_LIMIT = 200


@dataclass
class EaRefused(Exception):  # noqa: N818 -- exact name from the task interface
    """A 4xx the EA API returned for a request that retrying will not fix.

    Deliberately not `frozen=True`: a frozen dataclass exception raises
    `FrozenInstanceError` the moment something assigns its `__traceback__`,
    which happens as soon as it is raised through a `try`/`except`.
    """

    status: int
    code: str
    detail: str


@dataclass(frozen=True)
class Written:
    """The outcome of `ensure_element`."""

    id: UUID
    outcome: Literal["created", "existing", "completed"]


@dataclass(frozen=True)
class Metamodel:
    """The type values a flow may pass to `ensure_element` / `ensure_relationship`."""

    element_types: tuple[str, ...]
    relationship_types: tuple[str, ...]


def _refused(response: httpx.Response) -> EaRefused:
    """Build `EaRefused` from a 4xx response's `{error, detail}` envelope.

    `api/errors.py` is the only source of that shape; a body that does not
    match it (never observed from this API, but not this client's job to
    assume) falls back to a code naming the status instead of guessing.
    """
    try:
        body = response.json()
        return EaRefused(response.status_code, body["error"], body["detail"])
    except (ValueError, KeyError, TypeError):
        return EaRefused(response.status_code, f"http_{response.status_code}", response.text)


def _raise_for_unexpected_status(response: httpx.Response) -> NoReturn:
    """Every 4xx becomes `EaRefused`; a 5xx is left as `httpx.HTTPStatusError`.

    Called only once a method has already handled the status codes the API
    contract promises for a success; reaching this with a 2xx would itself be
    a contract break, so it is treated the same as an unhandled 5xx.
    """
    if response.is_client_error:
        raise _refused(response)
    response.raise_for_status()
    raise AssertionError(f"unexpected status {response.status_code} from the EA API")


class EaClient:
    """A thin, idempotent wrapper over the EA catalogue's HTTP API."""

    def __init__(self, http: httpx.Client) -> None:
        self.http = http

    def metamodel(self) -> Metamodel:
        """`GET /metamodel`, reduced to the type values a flow can pass back in."""
        response = self.http.get("/metamodel")
        if response.status_code != httpx.codes.OK:
            _raise_for_unexpected_status(response)
        body = response.json()
        return Metamodel(
            element_types=tuple(entry["value"] for entry in body["element_types"]),
            relationship_types=tuple(entry["value"] for entry in body["relationship_types"]),
        )

    def ensure_element(
        self, element_type: str, name: str, description: str, source: str
    ) -> Written:
        """Create the element, or converge onto the one already there.

        `source` is recorded as `properties.ingested_from` on creation only —
        a `PATCH` never touches `properties`, which *replaces* the whole map
        (`domain/model.py`), so writing it here would erase whatever else the
        catalogue already holds on a match.
        """
        response = self.http.post(
            "/elements",
            json={
                "element_type": element_type,
                "name": name,
                "description": description,
                "properties": {"ingested_from": source},
            },
        )
        if response.status_code == httpx.codes.CREATED:
            return Written(UUID(response.json()["id"]), "created")
        if response.status_code != httpx.codes.CONFLICT:
            _raise_for_unexpected_status(response)
        return self._resolve_duplicate_element(response, element_type, name, description)

    def _resolve_duplicate_element(
        self, conflict: httpx.Response, element_type: str, name: str, description: str
    ) -> Written:
        """After a 409, find the element `POST /elements` refused to recreate.

        `search` is a case-insensitive substring match, so "Billing" also
        returns "Billing Portal" — only the item whose `name` is exactly equal
        counts as the one the create collided with. Not found on any page: the
        409 stands, raised as the caller would have seen it directly.
        """
        offset = 0
        while True:
            page_response = self.http.get(
                "/elements",
                params={
                    "element_type": element_type,
                    "search": name,
                    "limit": _PAGE_LIMIT,
                    "offset": offset,
                },
            )
            if page_response.status_code != httpx.codes.OK:
                _raise_for_unexpected_status(page_response)
            page = page_response.json()
            match = next((item for item in page["items"] if item["name"] == name), None)
            if match is not None:
                return self._complete_or_existing(match, description)
            offset += _PAGE_LIMIT
            if offset >= page["total"]:
                raise _refused(conflict)

    def _complete_or_existing(self, existing: dict[str, object], description: str) -> Written:
        element_id = UUID(str(existing["id"]))
        if existing["description"] or not description:
            return Written(element_id, "existing")
        response = self.http.patch(f"/elements/{element_id}", json={"description": description})
        if response.status_code != httpx.codes.OK:
            _raise_for_unexpected_status(response)
        return Written(element_id, "completed")

    def ensure_relationship(
        self, relationship_type: str, source_id: UUID, target_id: UUID
    ) -> Literal["created", "existing"]:
        """Link the two elements, unless that exact link already exists."""
        offset = 0
        while True:
            response = self.http.get(
                "/relationships",
                params={
                    "element_id": str(source_id),
                    "relationship_type": relationship_type,
                    "limit": _PAGE_LIMIT,
                    "offset": offset,
                },
            )
            if response.status_code != httpx.codes.OK:
                _raise_for_unexpected_status(response)
            page = response.json()
            if any(UUID(item["target_id"]) == target_id for item in page):
                return "existing"
            if len(page) < _PAGE_LIMIT:
                break
            offset += _PAGE_LIMIT
        response = self.http.post(
            "/relationships",
            json={
                "relationship_type": relationship_type,
                "source_id": str(source_id),
                "target_id": str(target_id),
            },
        )
        if response.status_code != httpx.codes.CREATED:
            _raise_for_unexpected_status(response)
        return "created"


def ea_client(settings: Settings) -> EaClient:
    """One pooled client for the EA API, carrying its base URL and timeout."""
    return EaClient(
        httpx.Client(base_url=settings.ea_base_url, timeout=settings.ea_timeout_seconds)
    )
