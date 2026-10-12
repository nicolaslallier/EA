"""GitHub, Jira, Confluence and ServiceNow behind the `ExternalSystem` port — docs/adr/0073.

Adapters in the sense this package means (like `HttpEmbedder`): each implements the port the
domain declared, and each reaches its provider **only through an `EgressClient`**, which fixes
the host, checks the resolved address, bounds the answer and follows no redirect. What is
left to a provider class is the wire format — the path of an object, the shape of the JSON
that comes back — and the rules the egress client cannot know:

* **The scope is checked before any request.** A repository, a project or a table that is not
  in the target's allow-list is refused without a call; the identifier read back from the
  database is validated again, so a row altered by hand cannot become a request path.
* **Only a title and a state are kept.** The provider's JSON is read for exactly those
  fields; a body, a description or an address it returns is never copied. Titles are cleaned
  by `ExternalSnapshot.of`.
* **A provider's words never leave this module.** An error says that the provider did not
  answer usefully; the log says which target and which status, never the body, the title or
  the token. The token lives in the egress client's headers and nowhere else.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Collection, Mapping
from typing import TYPE_CHECKING, Any, ClassVar, Final

from ea.domain.egress import networks_from
from ea.domain.errors import (
    ExternalRefusedError,
    ExternalUnavailableError,
    ExternalWriteFailedError,
    ExternalWriteTimeoutError,
    InvalidExternalReferenceError,
)
from ea.domain.external import (
    ExternalKind,
    ExternalSnapshot,
    ExternalStatus,
    IssueDraft,
    scope_of,
    servicenow_status,
    servicenow_table,
    split_confluence_id,
    split_github_id,
    split_jira_key,
)
from ea.repositories.external_http import (
    EgressClient,
    EgressError,
    EgressResponse,
    Resolver,
    system_resolver,
)

if TYPE_CHECKING:
    import httpx

    from ea.core.config import ExternalTargetSettings

logger = logging.getLogger(__name__)

_GONE: Final = frozenset({404, 410})
_QUOTA: Final = frozenset({403, 429})
_UNAVAILABLE: Final = "the provider did not answer usefully"
_MAX_ISSUE_NUMBER: Final = 999_999_999


class _Provider:
    """What the four have in common: a target's name, its scope, and the status table."""

    kind: ClassVar[ExternalKind]
    label: ClassVar[str]

    def __init__(
        self,
        name: str,
        *,
        web_url: str,
        allow: Collection[str],
        writable: bool,
        egress: EgressClient,
    ) -> None:
        self._name = name
        self._web_url = web_url
        self._allow = frozenset(allow)
        self._writable = writable
        self._egress = egress

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self._name}>"

    @property
    def name(self) -> str:
        return self._name

    @property
    def web_url(self) -> str:
        return self._web_url

    @property
    def writable(self) -> bool:
        return self._writable

    async def aclose(self) -> None:
        await self._egress.aclose()

    # --- the shared way to ask and to read -------------------------------------------------

    def _unavailable(self, why: str, **fields: object) -> ExternalUnavailableError:
        """The error a caller sees — mute about the host and the provider's words."""
        logger.warning(
            "a provider could not be used: %s",
            why,
            extra={"action": "external_unavailable", "target": self._name, **fields},
        )
        return ExternalUnavailableError(_UNAVAILABLE)

    async def _ask(self, path: str, params: Mapping[str, str] | None = None) -> EgressResponse:
        try:
            return await self._egress.get(path, params=params)
        except EgressError as failure:
            raise self._unavailable(str(failure)) from None

    def _object(self, response: EgressResponse, *, what: str) -> dict[str, Any]:
        """The JSON object of a 200, a refusal for a 404/410, unavailable for anything else."""
        if response.status in _GONE:
            msg = f"{what} was not found on {self.label}"
            raise ExternalRefusedError(msg)
        self._expect_ok(response)
        try:
            payload = json.loads(response.body)
        except (ValueError, RecursionError):  # a deeply nested answer is not an object either
            raise self._unavailable("the answer is not JSON") from None
        if not isinstance(payload, dict):
            raise self._unavailable("the answer is not an object")
        return payload

    def _expect_ok(self, response: EgressResponse) -> None:
        if response.status in _QUOTA:
            logger.warning(
                "a provider refused the request: quota or permission",
                extra={
                    "action": "external_rate_limited",
                    "target": self._name,
                    "status": response.status,
                },
            )
            raise ExternalUnavailableError(_UNAVAILABLE)
        if response.status != 200:
            raise self._unavailable(f"status {response.status}", status=response.status)

    def allows(self, scope: str) -> bool:
        return scope in self._allow

    def _scope(self, kind_scope: str, what: str) -> None:
        """Refuse an object outside the allow-list before any request."""
        if kind_scope not in self._allow:
            msg = f"{what} is not in a scope this target may read"
            raise ExternalRefusedError(msg)

    def _identifier(self, external_id: str) -> str:
        """The stored identifier, validated again, and its scope checked."""
        try:
            scope = scope_of(self.kind, external_id)
        except InvalidExternalReferenceError:
            msg = f"that is not a {self.label} identifier"
            raise ExternalRefusedError(msg) from None
        if scope:
            self._scope(scope, "that object")
        return scope

    async def resolve(self, external_id: str) -> ExternalSnapshot:  # pragma: no cover
        raise NotImplementedError

    async def probe(self) -> None:  # pragma: no cover - overridden by every provider
        raise NotImplementedError

    async def create_issue(self, scope: str, draft: IssueDraft) -> str:
        msg = f"{self.label} targets cannot open tickets from here"
        raise ExternalWriteFailedError(msg)


class GithubIssues(_Provider):
    kind = ExternalKind.GITHUB_ISSUE
    label = "GitHub"

    async def resolve(self, external_id: str) -> ExternalSnapshot:
        self._identifier(external_id)
        repo, number = split_github_id(external_id)
        response = await self._ask(f"/repos/{repo}/issues/{number}")
        payload = self._object(response, what=external_id)
        title, state = payload.get("title"), payload.get("state")
        if not isinstance(title, str) or not isinstance(state, str):
            raise self._unavailable("the answer is not an issue")
        known = state in (ExternalStatus.OPEN.value, ExternalStatus.CLOSED.value)
        return ExternalSnapshot.of(
            title, ExternalStatus(state) if known else ExternalStatus.UNKNOWN
        )

    async def probe(self) -> None:
        """`/rate_limit` checks the token and the route without spending the quota."""
        self._expect_ok(await self._ask("/rate_limit"))

    async def create_issue(self, scope: str, draft: IssueDraft) -> str:
        if self._writable is not True:  # defence in depth: the service's gate comes first
            msg = "this target is not writable"
            raise ExternalWriteFailedError(msg)
        self._scope(scope, "that repository")
        try:
            response = await self._egress.post(
                f"/repos/{scope}/issues", json={"title": draft.title, "body": draft.body}
            )
        except EgressError as failure:
            logger.warning(
                "opening a ticket failed",
                extra={"action": "external_write_failed", "target": self._name},
            )
            if failure.timed_out:
                msg = "the provider did not answer in time; check it before trying again"
                raise ExternalWriteTimeoutError(msg) from None
            msg = "the provider could not be reached"
            raise ExternalWriteFailedError(msg) from None
        if response.status != 201:
            logger.warning(
                "a provider refused to open a ticket",
                extra={
                    "action": "external_write_failed",
                    "target": self._name,
                    "status": response.status,
                },
            )
            msg = "the provider refused or failed the request"
            raise ExternalWriteFailedError(msg)
        number = _issue_number(response.body)
        if number is None:
            logger.warning(
                "a provider created a ticket and answered unreadably",
                extra={"action": "external_write_unreadable", "target": self._name},
            )
            msg = "the ticket may exist but the answer was unreadable: check the provider"
            raise ExternalWriteFailedError(msg)
        return f"{scope}#{number}"


def _issue_number(body: bytes) -> int | None:
    try:
        number = json.loads(body).get("number")
    except (ValueError, AttributeError, RecursionError):
        return None
    if isinstance(number, bool) or not isinstance(number, int):
        return None
    return number if 0 < number <= _MAX_ISSUE_NUMBER else None


class JiraIssues(_Provider):
    kind = ExternalKind.JIRA_ISSUE
    label = "Jira"

    async def resolve(self, external_id: str) -> ExternalSnapshot:
        self._identifier(external_id)
        project, number = split_jira_key(external_id)
        response = await self._ask(
            f"/rest/api/3/issue/{project}-{number}", {"fields": "summary,status"}
        )
        fields = self._object(response, what=external_id).get("fields")
        if not isinstance(fields, dict) or not isinstance(fields.get("summary"), str):
            raise self._unavailable("the answer is not an issue")
        category = _walk(fields, "status", "statusCategory", "key")
        status = {"new": ExternalStatus.OPEN, "indeterminate": ExternalStatus.OPEN}.get(
            category if isinstance(category, str) else "",
            ExternalStatus.CLOSED if category == "done" else ExternalStatus.UNKNOWN,
        )
        return ExternalSnapshot.of(fields["summary"], status)

    async def probe(self) -> None:
        self._expect_ok(await self._ask("/rest/api/3/myself"))


class ConfluencePages(_Provider):
    kind = ExternalKind.CONFLUENCE_PAGE
    label = "Confluence"

    async def resolve(self, external_id: str) -> ExternalSnapshot:
        try:
            page = split_confluence_id(external_id)
        except InvalidExternalReferenceError:
            msg = "that is not a Confluence page identifier"
            raise ExternalRefusedError(msg) from None
        payload = self._object(await self._ask(f"/wiki/api/v2/pages/{page}"), what=external_id)
        title, state, space = payload.get("title"), payload.get("status"), payload.get("spaceId")
        if not isinstance(title, str) or not isinstance(state, str) or space is None:
            raise self._unavailable("the answer is not a page")
        # The space is only known from the answer: the title of a page outside the allowed
        # spaces is dropped here, so it is never cached.
        if (
            isinstance(space, bool)
            or not isinstance(space, str | int)
            or str(space) not in self._allow
        ):
            msg = "that page is not in a space this target may read"
            raise ExternalRefusedError(msg)
        if state == "current":
            status = ExternalStatus.OPEN
        elif state in {"archived", "trashed", "deleted"}:
            status = ExternalStatus.CLOSED
        else:
            status = ExternalStatus.UNKNOWN
        return ExternalSnapshot.of(title, status)

    async def probe(self) -> None:
        self._expect_ok(await self._ask("/wiki/api/v2/spaces", {"limit": "1"}))


class ServiceNowTickets(_Provider):
    kind = ExternalKind.SERVICENOW_TICKET
    label = "ServiceNow"

    async def resolve(self, external_id: str) -> ExternalSnapshot:
        self._identifier(external_id)
        table = servicenow_table(external_id)
        response = await self._ask(
            f"/api/now/table/{table}",
            {
                # The number is a validated identifier: no free text reaches a query.
                "sysparm_query": f"number={external_id}",
                "sysparm_fields": "number,short_description,state",
                "sysparm_limit": "1",
                "sysparm_exclude_reference_link": "true",
            },
        )
        rows = self._object(response, what=external_id).get("result")
        if not isinstance(rows, list):
            raise self._unavailable("the answer is not a list of tickets")
        if not rows:
            msg = f"{external_id} was not found on ServiceNow"
            raise ExternalRefusedError(msg)
        row = rows[0]
        title = row.get("short_description") if isinstance(row, dict) else None
        state = row.get("state") if isinstance(row, dict) else None
        if (
            not isinstance(title, str)
            or not isinstance(state, str | int)
            or isinstance(state, bool)
        ):
            raise self._unavailable("the answer is not a ticket")
        return ExternalSnapshot.of(title, servicenow_status(external_id, str(state)))

    async def probe(self) -> None:
        table = servicenow_table(f"{sorted(self._allow)[0]}0000001")
        self._expect_ok(
            await self._ask(
                f"/api/now/table/{table}",
                {"sysparm_limit": "1", "sysparm_fields": "number"},
            )
        )


def _walk(value: object, *keys: str) -> object:
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


_PROVIDERS: Final[dict[ExternalKind, type[_Provider]]] = {
    ExternalKind.GITHUB_ISSUE: GithubIssues,
    ExternalKind.JIRA_ISSUE: JiraIssues,
    ExternalKind.CONFLUENCE_PAGE: ConfluencePages,
    ExternalKind.SERVICENOW_TICKET: ServiceNowTickets,
}


def _headers(kind: ExternalKind, user: str, token: str) -> dict[str, str]:
    if kind is ExternalKind.GITHUB_ISSUE:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    basic = base64.b64encode(f"{user}:{token}".encode()).decode()
    return {"Authorization": f"Basic {basic}", "Accept": "application/json"}


def build_provider(
    name: str,
    target: ExternalTargetSettings,
    *,
    token: str,
    timeout: float,
    total_timeout: float,
    max_bytes: int,
    ca_cert: str | None,
    resolver: Resolver = system_resolver,
    transport: httpx.AsyncBaseTransport | None = None,
) -> _Provider:
    """The provider a declared target stands for, with its token in its egress headers only."""
    egress = EgressClient(
        target.api_url,
        headers=_headers(target.kind, target.user, token),
        ports=target.ports,
        allowed_networks=networks_from(target.allow_networks),
        timeout=timeout,
        total_timeout=total_timeout,
        max_bytes=max_bytes,
        ca_cert=ca_cert,
        resolver=resolver,
        transport=transport,
    )
    return _PROVIDERS[target.kind](
        name,
        web_url=target.web_url,
        allow=target.allow,
        writable=target.writable,
        egress=egress,
    )
