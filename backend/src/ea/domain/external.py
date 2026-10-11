"""External references: a link from an element to an object in another tool.

Pure, like `ipam.py`: nothing here opens a socket or imports a framework. What
the server accepts as an identifier, the address it builds from it, the scope an
allow-list is checked against, and when a cached title is old enough to be doubted
are all decided here, so the REST adapter, the MCP adapter and the service cannot
disagree. See docs/adr/0063.

**The stored address is built, never copied.** `parse_reference` returns the
canonical identifier and the URL derived from the configured web base; a pasted
URL is only ever *read* to extract the identifier, and one on another host is
refused. That, and the connectors calling only their configured API host, is what
keeps a user's text out of an outbound request (SSRF).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, NamedTuple
from urllib.parse import quote, urlsplit
from uuid import UUID, uuid4

from ea.domain.errors import InvalidExternalReferenceError

MAX_REFERENCE_LENGTH: Final = 2048
MAX_URL_LENGTH: Final = 2048
MAX_EXTERNAL_ID_LENGTH: Final = 2048
MAX_TITLE_LENGTH: Final = 500
MAX_CREATED_BY_LENGTH: Final = 200
MAX_TARGET_LENGTH: Final = 32
#: A written ticket: what a person may ask the server to open at a provider.
MAX_ISSUE_TITLE_LENGTH: Final = 256
MAX_ISSUE_BODY_LENGTH: Final = 8000

#: How many references one `refresh` of an element resolves — a provider's quota is not ours
#: to spend in one click.
MAX_REFRESH_BATCH: Final = 20
#: How many stale references one listing returns (the Prefect flow pages through them).
MAX_STALE_PAGE: Final = 100

#: A cached state older than this is shown as such. Not a setting: nothing argues for two
#: deployments disagreeing on it.
STALE_AFTER: Final = timedelta(hours=24)

#: A target's name, as written in the configuration and in a request.
TARGET_NAME_PATTERN: Final = r"[a-z0-9][a-z0-9-]{0,30}"


class ExternalKind(StrEnum):
    """Every kind of external object the catalogue can cite — the SPA asks, never copies."""

    URL = "url"
    GITHUB_ISSUE = "github_issue"
    JIRA_ISSUE = "jira_issue"
    CONFLUENCE_PAGE = "confluence_page"
    SERVICENOW_TICKET = "servicenow_ticket"


class ExternalStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


#: The kinds whose provider can open a ticket from here (the others are read-only).
WRITABLE_KINDS: Final = frozenset({ExternalKind.GITHUB_ISSUE})

#: The kinds a connector resolves. `url` is a link that is shown, never called.
RESOLVED_KINDS: Final = frozenset(ExternalKind) - {ExternalKind.URL}

#: `owner/repo`, lowercase — GitHub treats names case-insensitively, so the canonical form is
#: what makes the uniqueness constraint mean something.
GITHUB_REPO_PATTERN: Final = r"[a-z0-9](?:[a-z0-9-]{0,38})/[a-z0-9._-]{1,100}"
JIRA_PROJECT_PATTERN: Final = r"[A-Z][A-Z0-9_]{1,9}"
_NUMBER: Final = r"[1-9][0-9]{0,8}"
_GITHUB_SHORT: Final = re.compile(rf"(?P<repo>{GITHUB_REPO_PATTERN})#(?P<number>{_NUMBER})")
_GITHUB_PATH: Final = re.compile(
    rf"/(?P<repo>{GITHUB_REPO_PATTERN})/issues/(?P<number>{_NUMBER})/?"
)
_JIRA_KEY: Final = re.compile(rf"(?P<project>{JIRA_PROJECT_PATTERN})-(?P<number>{_NUMBER})")
_JIRA_PATH: Final = re.compile(rf"/browse/(?P<key>{JIRA_PROJECT_PATTERN}-{_NUMBER})/?")
_CONFLUENCE_ID: Final = re.compile(r"[1-9][0-9]{0,15}")
_CONFLUENCE_PATH: Final = re.compile(
    r"/wiki/spaces/[A-Za-z0-9~_.-]{1,255}/pages/(?P<id>[1-9][0-9]{0,15})(?:/[^/]{0,500})?/?"
)
#: Table prefix -> table, and which `state` values are open and closed there. Anything
#: else is `unknown`: a state outside the table is a customised instance, not a guess.
SERVICENOW_TABLES: Final = {
    "INC": ("incident", frozenset(range(1, 6)), frozenset({6, 7, 8})),
    "CHG": ("change_request", frozenset(range(-5, 1)), frozenset({3, 4})),
    "RITM": ("sc_req_item", frozenset({1, 2}), frozenset({3, 4, 7})),
    "PRB": ("problem", frozenset(range(101, 105)), frozenset({106, 107})),
}
_SERVICENOW_NUMBER: Final = re.compile(r"(?P<prefix>[A-Z]{3,4})(?P<digits>[0-9]{7,10})")
_HELP: Final = {
    ExternalKind.GITHUB_ISSUE: (
        "a GitHub issue is written owner/repo#123, or as the address of that issue "
        "on the configured GitHub"
    ),
    ExternalKind.JIRA_ISSUE: "a Jira issue is written PROJ-123, or as its /browse/ address",
    ExternalKind.CONFLUENCE_PAGE: (
        "a Confluence page is its number, or the address of a page of the configured site"
    ),
    ExternalKind.SERVICENOW_TICKET: (
        "a ServiceNow ticket is its number, such as INC0012345 (INC, CHG, RITM or PRB)"
    ),
}


class ParsedReference(NamedTuple):
    external_id: str
    url: str


def _refused(kind: ExternalKind) -> InvalidExternalReferenceError:
    return InvalidExternalReferenceError(_HELP[kind])


def _path_on(text: str, web_base: str) -> str | None:
    """The path of `text` if it is an address on the configured site, else `None`.

    Same scheme, same host, same port, no credentials: anything else is somebody else's site.
    """
    try:
        given, base = urlsplit(text), urlsplit(web_base)
        same_place = (given.scheme, given.hostname, given.port) == (
            base.scheme,
            base.hostname,
            base.port,
        )
        if given.username is not None or given.password is not None:
            return None
    except ValueError:
        return None
    return given.path if same_place else None


def _repo_is_sane(repo: str) -> bool:
    """`.` and `..` match the character class and would climb out of `/repos/`."""
    return repo.split("/", 1)[1] not in {".", ".."}


def split_github_id(external_id: str) -> tuple[str, int]:
    """`("owner/repo", number)` of a canonical identifier — re-checked on every read.

    A connector calls this on what it read from the database, not only on what a user
    typed: a row altered by hand must not become a request path.
    """
    match = _GITHUB_SHORT.fullmatch(external_id)
    if match is None or not _repo_is_sane(match["repo"]):
        raise _refused(ExternalKind.GITHUB_ISSUE)
    return match["repo"], int(match["number"])


def _parse_github(raw: str, web_base: str) -> ParsedReference:
    text = raw.strip()
    kind = ExternalKind.GITHUB_ISSUE
    if "://" in text:
        path = _path_on(text, web_base)
        match = _GITHUB_PATH.fullmatch(path.lower()) if path is not None else None
    else:
        match = _GITHUB_SHORT.fullmatch(text.lower())
    if match is None or not _repo_is_sane(match["repo"]):
        raise _refused(kind)
    repo, number = match["repo"], match["number"]
    return ParsedReference(f"{repo}#{number}", f"{web_base.rstrip('/')}/{repo}/issues/{number}")


def split_jira_key(external_id: str) -> tuple[str, int]:
    match = _JIRA_KEY.fullmatch(external_id)
    if match is None:
        raise _refused(ExternalKind.JIRA_ISSUE)
    return match["project"], int(match["number"])


def _parse_jira(raw: str, web_base: str) -> ParsedReference:
    text = raw.strip()
    if "://" in text:
        path = _path_on(text, web_base)
        found = _JIRA_PATH.fullmatch(path.upper().replace("/BROWSE/", "/browse/")) if path else None
        key = found["key"] if found else None
    else:
        key = text.upper()
    if key is None or _JIRA_KEY.fullmatch(key) is None:
        raise _refused(ExternalKind.JIRA_ISSUE)
    return ParsedReference(key, f"{web_base.rstrip('/')}/browse/{key}")


def split_confluence_id(external_id: str) -> int:
    if _CONFLUENCE_ID.fullmatch(external_id) is None:
        raise _refused(ExternalKind.CONFLUENCE_PAGE)
    return int(external_id)


def _parse_confluence(raw: str, web_base: str) -> ParsedReference:
    text = raw.strip()
    if "://" in text:
        path = _path_on(text, web_base)
        found = _CONFLUENCE_PATH.fullmatch(path) if path is not None else None
        page = found["id"] if found else None
    else:
        page = text
    if page is None or _CONFLUENCE_ID.fullmatch(page) is None:
        raise _refused(ExternalKind.CONFLUENCE_PAGE)
    return ParsedReference(page, f"{web_base.rstrip('/')}/wiki/pages/viewpage.action?pageId={page}")


def servicenow_table(number: str) -> str:
    """The table a ticket number lives in — checked again on every read of a stored one."""
    match = _SERVICENOW_NUMBER.fullmatch(number)
    if match is None or match["prefix"] not in SERVICENOW_TABLES:
        raise _refused(ExternalKind.SERVICENOW_TICKET)
    return SERVICENOW_TABLES[match["prefix"]][0]


def servicenow_status(number: str, state: str) -> ExternalStatus:
    """A `state` value translated per table; anything outside the table is `unknown`."""
    prefix = number[:3] if number[:3] in SERVICENOW_TABLES else number[:4]
    if prefix not in SERVICENOW_TABLES or len(state) > 8:
        return ExternalStatus.UNKNOWN
    try:
        value = int(state)
    except ValueError:
        return ExternalStatus.UNKNOWN
    _, opened, closed = SERVICENOW_TABLES[prefix]
    if value in closed:
        return ExternalStatus.CLOSED
    return ExternalStatus.OPEN if value in opened else ExternalStatus.UNKNOWN


def _parse_servicenow(raw: str, web_base: str) -> ParsedReference:
    number = raw.strip().upper()
    servicenow_table(number)
    table = servicenow_table(number)
    inner = quote(f"/{table}.do?sysparm_query=number={number}", safe="")
    return ParsedReference(number, f"{web_base.rstrip('/')}/nav_to.do?uri={inner}")


def _parse_free_url(raw: str) -> ParsedReference:
    text = raw.strip()
    plain = (
        0 < len(text) <= MAX_URL_LENGTH
        and text.isascii()
        and not any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in text)
    )
    if not plain:
        raise InvalidExternalReferenceError(
            "a link is an http or https address, at most 2048 characters, "
            "ASCII only (encode the rest), with no spaces"
        )
    try:
        parts = urlsplit(text)
        port_ok = parts.port is None or parts.port > 0
    except ValueError as error:
        raise InvalidExternalReferenceError("that is not a valid web address") from error
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or not port_ok
    ):
        raise InvalidExternalReferenceError(
            "a link is an http or https address with a host and no embedded credentials"
        )
    return ParsedReference(text, text)


def parse_reference(kind: ExternalKind, raw: str, *, web_base: str | None) -> ParsedReference:
    """The canonical identifier and the address to show, for what a user wrote.

    `web_base` is the target's configured site, `None` for a free link.
    """
    if kind is ExternalKind.URL:
        return _parse_free_url(raw)
    if web_base is None:
        raise InvalidExternalReferenceError("this kind needs a target")
    if len(raw) > MAX_REFERENCE_LENGTH:
        raise _refused(kind)
    parsers = {
        ExternalKind.GITHUB_ISSUE: _parse_github,
        ExternalKind.JIRA_ISSUE: _parse_jira,
        ExternalKind.CONFLUENCE_PAGE: _parse_confluence,
        ExternalKind.SERVICENOW_TICKET: _parse_servicenow,
    }
    return parsers[kind](raw, web_base)


def scope_of(kind: ExternalKind, external_id: str) -> str:
    """What an allow-list names for this object: a repository, a project, a table prefix.

    A Confluence page's space is not in its identifier: the connector checks it on the answer.
    """
    if kind is ExternalKind.GITHUB_ISSUE:
        return split_github_id(external_id)[0]
    if kind is ExternalKind.JIRA_ISSUE:
        return split_jira_key(external_id)[0]
    if kind is ExternalKind.SERVICENOW_TICKET:
        servicenow_table(external_id)
        return external_id[:3] if external_id[:3] in SERVICENOW_TABLES else external_id[:4]
    return ""


def clean_title(raw: str) -> str:
    """A third party's title as plain, bounded, storable text.

    Control characters become spaces (a NUL cannot be stored in PostgreSQL and a newline
    breaks a table row), formatting characters that reorder text on screen are dropped, and
    the result is cut at `MAX_TITLE_LENGTH`. Markup is left alone on purpose: it is data,
    rendered as text by the SPA.
    """
    kept = []
    for char in raw:
        category = unicodedata.category(char)
        if category in {"Cc", "Zl", "Zp"}:
            kept.append(" ")
        elif category != "Cf":
            kept.append(char)
    return " ".join("".join(kept).split())[:MAX_TITLE_LENGTH]


@dataclass(frozen=True, slots=True)
class ExternalSnapshot:
    """What a provider said about an object — a title and a state, never a body."""

    title: str
    status: ExternalStatus

    @classmethod
    def of(cls, title: str, status: ExternalStatus) -> ExternalSnapshot:
        return cls(title=clean_title(title), status=status)


@dataclass(frozen=True, slots=True)
class ExternalReference:
    """One element's link to one external object, with the last thing known about it."""

    id: UUID
    element_id: UUID
    kind: ExternalKind
    #: The name of the configured target that resolves it; empty for a free link.
    target: str
    external_id: str
    url: str
    title: str
    status: ExternalStatus
    synced_at: datetime | None
    created_by: str
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        element_id: UUID,
        kind: ExternalKind,
        target: str,
        external_id: str,
        url: str,
        snapshot: ExternalSnapshot | None,
        created_by: str,
        now: datetime,
        reference_id: UUID | None = None,
    ) -> ExternalReference:
        return cls(
            id=reference_id or uuid4(),
            element_id=element_id,
            kind=kind,
            target=target,
            external_id=external_id,
            url=url,
            title=snapshot.title if snapshot else "",
            status=snapshot.status if snapshot else ExternalStatus.UNKNOWN,
            synced_at=now if snapshot else None,
            created_by=created_by[:MAX_CREATED_BY_LENGTH],
            created_at=now,
        )

    def with_snapshot(self, snapshot: ExternalSnapshot, *, now: datetime) -> ExternalReference:
        return replace(self, title=snapshot.title, status=snapshot.status, synced_at=now)

    def is_stale(self, now: datetime) -> bool:
        if self.kind not in RESOLVED_KINDS:
            return False
        return self.synced_at is None or now - self.synced_at > STALE_AFTER


@dataclass(frozen=True, slots=True)
class ExternalView:
    """A reference and the answer to « should I doubt it? », computed by the service's clock."""

    reference: ExternalReference
    stale: bool


@dataclass(frozen=True, slots=True)
class TargetStatus:
    """One declared target, as a caller may see it: a name and a kind, never an address."""

    name: str
    kind: ExternalKind
    active: bool
    writable: bool


@dataclass(frozen=True, slots=True)
class IssueDraft:
    """What would be sent to a provider to open a ticket — a title and a text, bounded."""

    title: str
    body: str

    @classmethod
    def of(cls, title: str, body: str) -> IssueDraft:
        cleaned = clean_title(title)[:MAX_ISSUE_TITLE_LENGTH]
        text = "".join(c for c in body if c in "\n\t" or unicodedata.category(c)[0] != "C")
        if not cleaned:
            raise InvalidExternalReferenceError("a ticket needs a title")
        if len(text) > MAX_ISSUE_BODY_LENGTH:
            raise InvalidExternalReferenceError(
                f"a ticket text is at most {MAX_ISSUE_BODY_LENGTH} characters"
            )
        return cls(title=cleaned, body=text)


def clean_scope(kind: ExternalKind, scope: str) -> str:
    """The scope a ticket is opened in, in the spelling an allow-list compares in."""
    found = scope.strip().lower()
    if (
        kind is ExternalKind.GITHUB_ISSUE
        and re.fullmatch(GITHUB_REPO_PATTERN, found)
        and _repo_is_sane(found)
    ):
        return found
    raise InvalidExternalReferenceError("a repository is written owner/repo")


@dataclass(frozen=True, slots=True)
class IssuePlan:
    """What opening a ticket does (``dry_run``) or did: where, with which words, and the link."""

    dry_run: bool
    target: str
    scope: str
    title: str
    body: str
    view: ExternalView | None
