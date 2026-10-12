"""The four providers, against a transport that never leaves the process.

What matters is what a provider will *not* do: call another host, follow a redirect, read
an unbounded body, trust an unexpected answer, cache a body, or let the token reach a log or
a message (docs/adr/0073). The wire format of each is checked once; the refusals are shared
and run against all four.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import pytest

from ea.domain.egress import networks_from
from ea.domain.errors import (
    ExternalRefusedError,
    ExternalUnavailableError,
    ExternalWriteFailedError,
    ExternalWriteTimeoutError,
)
from ea.domain.external import (
    MAX_TITLE_LENGTH,
    ExternalKind,
    ExternalSnapshot,
    ExternalStatus,
    IssueDraft,
)
from ea.repositories.external_http import EgressClient
from ea.repositories.external_providers import (
    ConfluencePages,
    GithubIssues,
    JiraIssues,
    ServiceNowTickets,
    build_provider,
)

TOKEN = "tok_this_must_never_be_printed"
PUBLIC = "93.184.216.34"
Handler = Callable[[httpx.Request], httpx.Response]


async def _resolve(host: str, port: int) -> list[str]:
    return [PUBLIC]


def egress(handler: Handler, url: str, *, headers: dict[str, str] | None = None) -> EgressClient:
    return EgressClient(
        url,
        headers=headers or {"Authorization": f"Bearer {TOKEN}"},
        ports=(443,),
        allowed_networks=networks_from([]),
        timeout=2,
        total_timeout=5,
        max_bytes=100_000,
        resolver=_resolve,
        transport=httpx.MockTransport(handler),
    )


@dataclass
class Case:
    """How to build one provider, an identifier it knows, and the answers it understands."""

    kind: ExternalKind
    make: Callable[[Handler], object]
    identifier: str
    open_answer: dict[str, object]
    closed_answer: dict[str, object]
    title: str
    path: str


def github(handler: Handler, *, allow: tuple[str, ...] = ("acme/billing",)) -> GithubIssues:
    return GithubIssues(
        "main",
        web_url="https://github.test",
        allow=allow,
        writable=True,
        egress=egress(handler, "https://api.github.test"),
    )


def jira(handler: Handler, *, allow: tuple[str, ...] = ("PROJ",)) -> JiraIssues:
    return JiraIssues(
        "jira",
        web_url="https://acme.atlassian.test",
        allow=allow,
        writable=False,
        egress=egress(handler, "https://acme.atlassian.test"),
    )


def confluence(handler: Handler, *, allow: tuple[str, ...] = ("777",)) -> ConfluencePages:
    return ConfluencePages(
        "wiki",
        web_url="https://acme.atlassian.test",
        allow=allow,
        writable=False,
        egress=egress(handler, "https://acme.atlassian.test"),
    )


def servicenow(handler: Handler, *, allow: tuple[str, ...] = ("INC",)) -> ServiceNowTickets:
    return ServiceNowTickets(
        "snow",
        web_url="https://acme.service-now.test",
        allow=allow,
        writable=False,
        egress=egress(handler, "https://acme.service-now.test"),
    )


CASES = {
    "github": Case(
        ExternalKind.GITHUB_ISSUE,
        github,
        "acme/billing#12",
        {"title": "Fix rounding", "state": "open"},
        {"title": "Fix rounding", "state": "closed"},
        "Fix rounding",
        "/repos/acme/billing/issues/12",
    ),
    "jira": Case(
        ExternalKind.JIRA_ISSUE,
        jira,
        "PROJ-123",
        {"fields": {"summary": "Fix rounding", "status": {"statusCategory": {"key": "new"}}}},
        {"fields": {"summary": "Fix rounding", "status": {"statusCategory": {"key": "done"}}}},
        "Fix rounding",
        "/rest/api/3/issue/PROJ-123",
    ),
    "confluence": Case(
        ExternalKind.CONFLUENCE_PAGE,
        confluence,
        "12345",
        {"title": "Fix rounding", "status": "current", "spaceId": "777"},
        {"title": "Fix rounding", "status": "archived", "spaceId": "777"},
        "Fix rounding",
        "/wiki/api/v2/pages/12345",
    ),
    "servicenow": Case(
        ExternalKind.SERVICENOW_TICKET,
        servicenow,
        "INC0012345",
        {"result": [{"number": "INC0012345", "short_description": "Fix rounding", "state": "2"}]},
        {"result": [{"number": "INC0012345", "short_description": "Fix rounding", "state": "7"}]},
        "Fix rounding",
        "/api/now/table/incident",
    ),
}
EVERY = pytest.mark.parametrize("case", CASES.values(), ids=CASES.keys())


def answering(case: Case, response: httpx.Response) -> tuple[object, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    return case.make(handler), seen


@pytest.mark.asyncio
class TestResolving:
    @EVERY
    async def test_an_open_and_a_closed_object_give_their_title_and_state(self, case: Case) -> None:
        opened, seen = answering(case, httpx.Response(200, json=case.open_answer))
        closed, _ = answering(case, httpx.Response(200, json=case.closed_answer))

        assert await opened.resolve(case.identifier) == ExternalSnapshot(  # type: ignore[attr-defined]
            case.title, ExternalStatus.OPEN
        )
        assert (await closed.resolve(case.identifier)).status is ExternalStatus.CLOSED  # type: ignore[attr-defined]
        assert seen[0].url.path == case.path
        assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"

    @EVERY
    async def test_markup_and_length_are_kept_as_bounded_text(self, case: Case) -> None:
        text = "<b>" + "x" * 900
        body = json.dumps(case.open_answer).replace(case.title, text)
        provider, _ = answering(case, httpx.Response(200, content=body.encode()))

        snapshot = await provider.resolve(case.identifier)  # type: ignore[attr-defined]

        assert len(snapshot.title) == MAX_TITLE_LENGTH
        assert snapshot.title.startswith("<b>")

    @EVERY
    @pytest.mark.parametrize("status", [404, 410])
    async def test_an_object_the_provider_says_is_not_there_is_refused(
        self, case: Case, status: int
    ) -> None:
        provider, _ = answering(case, httpx.Response(status))

        with pytest.raises(ExternalRefusedError):
            await provider.resolve(case.identifier)  # type: ignore[attr-defined]

    @EVERY
    @pytest.mark.parametrize("status", [301, 302, 401, 403, 429, 500, 502])
    async def test_every_other_answer_means_the_provider_could_not_be_asked(
        self, case: Case, status: int
    ) -> None:
        provider, _ = answering(
            case, httpx.Response(status, headers={"location": "https://evil.test/"})
        )

        with pytest.raises(ExternalUnavailableError):
            await provider.resolve(case.identifier)  # type: ignore[attr-defined]

    @EVERY
    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(200, content=b"<html>not json</html>"),
            httpx.Response(200, json=[]),
            httpx.Response(200, json="text"),
            httpx.Response(200, json={}),
            httpx.Response(200, json={"title": 5, "state": 1, "fields": 3, "result": 4}),
            httpx.Response(200, json={"result": [], "fields": {}}),
        ],
        ids=["html", "list", "string", "empty", "wrong-types", "nothing"],
    )
    async def test_an_answer_that_is_not_what_was_asked_for_is_never_cached(
        self, case: Case, response: httpx.Response
    ) -> None:
        provider, _ = answering(case, response)

        with pytest.raises((ExternalUnavailableError, ExternalRefusedError)):
            await provider.resolve(case.identifier)  # type: ignore[attr-defined]

    @EVERY
    async def test_a_timeout_and_a_network_error_are_unavailable(self, case: Case) -> None:
        def slow(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        def broken(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        for handler in (slow, broken):
            with pytest.raises(ExternalUnavailableError):
                await case.make(handler).resolve(case.identifier)  # type: ignore[attr-defined]

    async def test_a_strange_state_is_unknown(self) -> None:
        provider, _ = answering(
            CASES["github"], httpx.Response(200, json={"title": "t", "state": "merged-ish"})
        )

        assert (await provider.resolve("acme/billing#1")).status is ExternalStatus.UNKNOWN  # type: ignore[attr-defined]


DEEP = b"[" * 40_000 + b"]" * 40_000


@pytest.mark.asyncio
class TestHostileJson:
    @EVERY
    async def test_an_absurdly_nested_answer_is_unavailable_not_a_crash(self, case: Case) -> None:
        provider, _ = answering(case, httpx.Response(200, content=DEEP))

        with pytest.raises(ExternalUnavailableError):
            await provider.resolve(case.identifier)  # type: ignore[attr-defined]

    async def test_after_a_201_it_is_the_check_the_provider_message_not_a_crash(self) -> None:
        provider = github(lambda r: httpx.Response(201, content=DEEP))

        with pytest.raises(ExternalWriteFailedError, match="check"):
            await provider.create_issue("acme/billing", IssueDraft.of("t", ""))


@pytest.mark.asyncio
class TestTheWireFormats:
    async def test_jira_categories_translate_and_a_custom_one_is_unknown(self) -> None:
        def with_category(key: str) -> JiraIssues:
            return jira(
                lambda r: httpx.Response(
                    200,
                    json={"fields": {"summary": "s", "status": {"statusCategory": {"key": key}}}},
                )
            )

        assert (
            await with_category("indeterminate").resolve("PROJ-1")
        ).status is ExternalStatus.OPEN
        assert (await with_category("done").resolve("PROJ-1")).status is ExternalStatus.CLOSED
        assert (await with_category("odd").resolve("PROJ-1")).status is ExternalStatus.UNKNOWN

    async def test_jira_asks_only_for_the_summary_and_the_status(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=CASES["jira"].open_answer)

        await jira(handler).resolve("PROJ-1")

        assert seen[0].url.params["fields"] == "summary,status"

    async def test_confluence_refuses_a_page_outside_the_allowed_spaces_and_caches_nothing(
        self,
    ) -> None:
        other = httpx.Response(200, json={"title": "secret", "status": "current", "spaceId": "999"})
        provider, _ = answering(CASES["confluence"], other)

        with pytest.raises(ExternalRefusedError) as raised:
            await provider.resolve("12345")  # type: ignore[attr-defined]

        assert "secret" not in str(raised.value)

    async def test_servicenow_builds_the_query_from_the_validated_number_only(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=CASES["servicenow"].open_answer)

        provider = servicenow(handler)
        await provider.resolve("INC0012345")

        assert seen[0].url.params["sysparm_query"] == "number=INC0012345"
        assert seen[0].url.params["sysparm_limit"] == "1"
        assert seen[0].url.params["sysparm_fields"] == "number,short_description,state"
        with pytest.raises(ExternalRefusedError):
            await provider.resolve("INC0012345^ORDERBYDESCsys_id")
        assert len(seen) == 1

    async def test_servicenow_returns_the_state_translated_per_table(self) -> None:
        provider, _ = answering(
            CASES["servicenow"],
            httpx.Response(
                200, json={"result": [{"short_description": "x", "state": "7", "number": "INC1"}]}
            ),
        )

        assert (await provider.resolve("INC0012345")).status is ExternalStatus.CLOSED  # type: ignore[attr-defined]


@pytest.mark.asyncio
class TestScopes:
    @pytest.mark.parametrize(
        ("make", "identifier"),
        [
            (github, "acme/payroll#1"),
            (jira, "HR-1"),
            (servicenow, "CHG0000001"),
        ],
    )
    async def test_an_object_off_the_allow_list_is_refused_without_any_request(
        self, make: Callable[[Handler], object], identifier: str
    ) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={})

        with pytest.raises(ExternalRefusedError):
            await make(handler).resolve(identifier)  # type: ignore[attr-defined]

        assert seen == []

    @pytest.mark.parametrize(
        ("make", "tampered"),
        [
            (github, "acme/../x#1"),
            (github, "acme/billing#1/../../x"),
            (jira, "PROJ-1/../x"),
            (confluence, "12/../x"),
            (servicenow, "INC0012345^x"),
        ],
    )
    async def test_an_identifier_altered_in_storage_never_becomes_a_path(
        self, make: Callable[[Handler], object], tampered: str
    ) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={})

        with pytest.raises(ExternalRefusedError):
            await make(handler).resolve(tampered)  # type: ignore[attr-defined]

        assert seen == []


@pytest.mark.asyncio
class TestWhatTheyWillNotDo:
    @EVERY
    async def test_a_redirect_is_not_followed_and_no_other_host_is_ever_called(
        self, case: Case
    ) -> None:
        hosts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hosts.append(request.headers["host"])
            return httpx.Response(302, headers={"location": "https://evil.test/steal"})

        with pytest.raises(ExternalUnavailableError):
            await case.make(handler).resolve(case.identifier)  # type: ignore[attr-defined]

        assert len(hosts) == 1
        assert "evil" not in hosts[0]

    @EVERY
    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(401, text=f"bad credentials {TOKEN}"),
            httpx.Response(403, text=f"limit for {TOKEN}"),
            httpx.Response(429),
            httpx.Response(500, text=f"Bearer {TOKEN}"),
            httpx.Response(200, content=b"not json"),
            httpx.Response(404, text=TOKEN),
        ],
    )
    async def test_the_token_and_the_providers_words_reach_neither_the_log_nor_a_message(
        self, case: Case, response: httpx.Response, caplog: pytest.LogCaptureFixture
    ) -> None:
        provider, _ = answering(case, response)

        with (
            caplog.at_level(logging.DEBUG),
            pytest.raises((ExternalUnavailableError, ExternalRefusedError)) as raised,
        ):
            await provider.resolve(case.identifier)  # type: ignore[attr-defined]

        assert TOKEN not in caplog.text
        assert "credentials" not in caplog.text
        assert TOKEN not in str(raised.value) + repr(raised.value)
        assert "credentials" not in str(raised.value)

    @EVERY
    @pytest.mark.parametrize("status", [403, 429])
    async def test_a_quota_refusal_is_logged_under_its_own_action(
        self, case: Case, status: int, caplog: pytest.LogCaptureFixture
    ) -> None:
        provider, _ = answering(case, httpx.Response(status))

        with caplog.at_level(logging.WARNING), pytest.raises(ExternalUnavailableError):
            await provider.resolve(case.identifier)  # type: ignore[attr-defined]

        assert "external_rate_limited" in [getattr(r, "action", None) for r in caplog.records]

    @EVERY
    async def test_the_logs_name_the_target_and_never_the_object_titles(
        self, case: Case, caplog: pytest.LogCaptureFixture
    ) -> None:
        provider, _ = answering(case, httpx.Response(500, text="Fix rounding"))

        with caplog.at_level(logging.DEBUG), pytest.raises(ExternalUnavailableError):
            await provider.resolve(case.identifier)  # type: ignore[attr-defined]

        assert "Fix rounding" not in caplog.text


@pytest.mark.asyncio
class TestProbing:
    @EVERY
    async def test_a_token_the_provider_accepts_passes(self, case: Case) -> None:
        provider, seen = answering(
            case,
            httpx.Response(200, json={"result": [], "values": [], "resources": {}, "results": []}),
        )

        await provider.probe()  # type: ignore[attr-defined]

        assert len(seen) == 1

    @EVERY
    @pytest.mark.parametrize("status", [401, 403, 500])
    async def test_a_token_it_refuses_fails_the_probe(self, case: Case, status: int) -> None:
        provider, _ = answering(case, httpx.Response(status))

        with pytest.raises(ExternalUnavailableError):
            await provider.probe()  # type: ignore[attr-defined]


@pytest.mark.asyncio
class TestOpeningAnIssue:
    DRAFT = IssueDraft.of("Rounding is wrong", "Details here")

    async def test_it_posts_once_to_the_repository_of_the_scope_and_returns_the_identifier(
        self,
    ) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(201, json={"number": 42, "html_url": "https://evil.test/x"})

        identifier = await github(handler).create_issue("acme/billing", self.DRAFT)

        assert identifier == "acme/billing#42"  # built here: the provider's address is ignored
        (request,) = seen
        assert (request.method, request.url.path) == ("POST", "/repos/acme/billing/issues")
        assert json.loads(request.content) == {"title": "Rounding is wrong", "body": "Details here"}

    async def test_a_scope_off_the_list_is_refused_before_any_request(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(201, json={"number": 1})

        with pytest.raises(ExternalRefusedError):
            await github(handler).create_issue("acme/payroll", self.DRAFT)

        assert seen == []

    @pytest.mark.parametrize("status", [401, 403, 404, 422, 500, 302])
    async def test_a_refusal_or_an_error_is_a_bad_gateway_without_the_providers_words(
        self, status: int
    ) -> None:
        provider = github(lambda r: httpx.Response(status, text=f"nope {TOKEN} secret-words"))

        with pytest.raises(ExternalWriteFailedError) as raised:
            await provider.create_issue("acme/billing", self.DRAFT)

        assert "secret-words" not in str(raised.value)
        assert TOKEN not in str(raised.value)

    async def test_a_timeout_is_a_gateway_timeout_and_is_not_retried(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            raise httpx.ReadTimeout("slow", request=request)

        with pytest.raises(ExternalWriteTimeoutError):
            await github(handler).create_issue("acme/billing", self.DRAFT)

        assert len(seen) == 1

    @pytest.mark.parametrize("body", [{}, {"number": "x"}, {"number": 0}, {"number": 10**12}, []])
    async def test_a_created_answer_without_a_usable_number_asks_the_caller_to_check(
        self, body: object
    ) -> None:
        provider = github(lambda r: httpx.Response(201, json=body))

        with pytest.raises(ExternalWriteFailedError, match="check"):
            await provider.create_issue("acme/billing", self.DRAFT)

    async def test_a_provider_not_declared_writable_refuses_before_any_request(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(201, json={"number": 1})

        readonly = GithubIssues(
            "ro",
            web_url="https://github.test",
            allow=("acme/billing",),
            writable=False,
            egress=egress(handler, "https://api.github.test"),
        )

        with pytest.raises(ExternalWriteFailedError, match="writable"):
            await readonly.create_issue("acme/billing", self.DRAFT)

        assert seen == []

    async def test_a_kind_without_a_writer_says_so(self) -> None:
        with pytest.raises(ExternalWriteFailedError):
            await jira(lambda r: httpx.Response(201)).create_issue("PROJ", self.DRAFT)


class TestAuthentication:
    def test_jira_and_servicenow_send_basic_credentials_built_once(self) -> None:
        from ea.core.config import ExternalTargetSettings

        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=CASES["jira"].open_answer)

        target = ExternalTargetSettings(
            kind=ExternalKind.JIRA_ISSUE,
            api_url="https://acme.atlassian.test",
            web_url="https://acme.atlassian.test",
            allow=["PROJ"],
            user="bot@acme.test",
        )
        import asyncio

        provider = build_provider(
            "jira",
            target,
            token=TOKEN,
            timeout=1,
            total_timeout=2,
            max_bytes=10_000,
            ca_cert=None,
            resolver=_resolve,
            transport=httpx.MockTransport(handler),
        )
        asyncio.run(provider.resolve("PROJ-1"))

        expected = base64.b64encode(f"bot@acme.test:{TOKEN}".encode()).decode()
        assert seen[0].headers["authorization"] == f"Basic {expected}"
        assert TOKEN not in repr(provider)

    def test_a_target_with_a_free_link_kind_has_no_provider(self) -> None:
        assert ExternalKind.URL not in {
            GithubIssues.kind,
            JiraIssues.kind,
            ConfluencePages.kind,
            ServiceNowTickets.kind,
        }
