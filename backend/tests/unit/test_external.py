"""What an external reference is, and what the server accepts as one.

Pure rules, no I/O: the shape of an identifier per kind, the address the server
builds from it, the scope it falls in, and when a cached title is old enough to be
doubted. See docs/adr/0062.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ea.domain.errors import InvalidExternalReferenceError
from ea.domain.external import (
    MAX_TITLE_LENGTH,
    STALE_AFTER,
    ExternalKind,
    ExternalReference,
    ExternalSnapshot,
    ExternalStatus,
    ParsedReference,
    clean_title,
    parse_reference,
    scope_of,
    servicenow_status,
    servicenow_table,
    split_github_id,
)

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
GITHUB = ExternalKind.GITHUB_ISSUE
JIRA = ExternalKind.JIRA_ISSUE
WEB = "https://github.test"
CANONICAL = ParsedReference("acme/billing#12", "https://github.test/acme/billing/issues/12")


class TestGithubReferences:
    @pytest.mark.parametrize(
        "raw",
        [
            "acme/billing#12",
            "Acme/Billing#12",
            "  acme/billing#12  ",
            "https://github.test/acme/billing/issues/12",
            "https://github.test/Acme/Billing/issues/12/",
            "https://github.test/acme/billing/issues/12#issuecomment-5",
            "https://github.test/acme/billing/issues/12?notification_referrer_id=x",
        ],
    )
    def test_every_accepted_spelling_gives_the_same_canonical_reference(self, raw: str) -> None:
        assert parse_reference(GITHUB, raw, web_base=WEB) == CANONICAL

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "acme/billing",
            "acme/billing#0",
            "acme/billing#-1",
            "acme/billing#12abc",
            "acme/billing#1234567890",
            "acme#12",
            "a/b/c#1",
            "acme/..#1",
            "acme/.#1",
            "-acme/billing#1",
            "acme/bill ing#1",
            "https://evil.test/acme/billing/issues/12",
            "https://github.test.evil.test/acme/billing/issues/12",
            "http://github.test/acme/billing/issues/12",
            "https://user:pw@github.test/acme/billing/issues/12",
            "https://@github.test/acme/billing/issues/12",
            "https://github.test:8443/acme/billing/issues/12",
            "https://github.test/acme/billing/pull/12",
            "https://github.test/acme/billing/issues/12/../../x",
            "https://github.test/acme/../issues/1",
            "acme/bill\ning#1",
            "x" * 5000,
        ],
    )
    def test_everything_else_is_refused_before_anything_is_built(self, raw: str) -> None:
        with pytest.raises(InvalidExternalReferenceError):
            parse_reference(GITHUB, raw, web_base=WEB)

    def test_the_stored_url_comes_from_the_configured_base_not_from_the_input(self) -> None:
        parsed = parse_reference(
            GITHUB, "https://GITHUB.test/acme/billing/issues/12", web_base="https://github.test/"
        )

        assert parsed.url == "https://github.test/acme/billing/issues/12"

    def test_an_identifier_read_back_from_storage_is_checked_again(self) -> None:
        assert split_github_id("acme/billing#12") == ("acme/billing", 12)
        for tampered in ("acme/../x#1", "acme/billing#1/../../x", "Acme/Billing#1", "x"):
            with pytest.raises(InvalidExternalReferenceError):
                split_github_id(tampered)


class TestOtherProviders:
    def test_a_jira_key_is_upper_cased_and_its_address_built(self) -> None:
        base = "https://acme.atlassian.test"
        for raw in ("proj-123", " PROJ-123 ", "https://acme.atlassian.test/browse/proj-123"):
            assert parse_reference(JIRA, raw, web_base=base) == ParsedReference(
                "PROJ-123", "https://acme.atlassian.test/browse/PROJ-123"
            )

    @pytest.mark.parametrize(
        "raw",
        [
            "PROJ",
            "P-1",
            "PROJ-0",
            "PROJ-1;x",
            "1PROJ-1",
            "https://evil.test/browse/PROJ-1",
            "PROJ-1/x",
        ],
    )
    def test_a_malformed_jira_key_is_refused(self, raw: str) -> None:
        with pytest.raises(InvalidExternalReferenceError):
            parse_reference(JIRA, raw, web_base="https://acme.atlassian.test")

    def test_a_confluence_page_is_its_number_or_the_address_of_a_page_of_the_site(self) -> None:
        base = "https://acme.atlassian.test"
        expected = ParsedReference("12345", f"{base}/wiki/pages/viewpage.action?pageId=12345")
        for raw in (
            "12345",
            f"{base}/wiki/spaces/ENG/pages/12345/Some+title",
            f"{base}/wiki/spaces/ENG/pages/12345",
        ):
            assert parse_reference(ExternalKind.CONFLUENCE_PAGE, raw, web_base=base) == expected
        for bad in (
            "abc",
            "0",
            "12 34",
            f"{base}/wiki/spaces/ENG/x/12345",
            "https://x.test/wiki/spaces/E/pages/1",
        ):
            with pytest.raises(InvalidExternalReferenceError):
                parse_reference(ExternalKind.CONFLUENCE_PAGE, bad, web_base=base)

    def test_a_servicenow_number_picks_its_table_and_nothing_free_reaches_a_query(self) -> None:
        base = "https://acme.service-now.test"
        parsed = parse_reference(ExternalKind.SERVICENOW_TICKET, " inc0012345 ", web_base=base)

        assert parsed.external_id == "INC0012345"
        assert parsed.url.startswith(base + "/nav_to.do?uri=")
        assert servicenow_table("INC0012345") == "incident"
        assert servicenow_table("CHG0000001") == "change_request"
        for bad in (
            "INC1",
            "XYZ0012345",
            "INC0012345^x=1",
            "INC0012345 OR 1",
            "inc00123456789012",
        ):
            with pytest.raises(InvalidExternalReferenceError):
                parse_reference(ExternalKind.SERVICENOW_TICKET, bad, web_base=base)

    def test_the_scope_is_what_an_allow_list_names(self) -> None:
        assert scope_of(GITHUB, "acme/billing#12") == "acme/billing"
        assert scope_of(JIRA, "PROJ-123") == "PROJ"
        assert scope_of(ExternalKind.SERVICENOW_TICKET, "INC0012345") == "INC"
        assert scope_of(ExternalKind.CONFLUENCE_PAGE, "12345") == ""

    @pytest.mark.parametrize(
        ("number", "state", "expected"),
        [
            ("INC0000001", "2", ExternalStatus.OPEN),
            ("INC0000001", "7", ExternalStatus.CLOSED),
            ("CHG0000001", "-5", ExternalStatus.OPEN),
            ("CHG0000001", "3", ExternalStatus.CLOSED),
            ("INC0000001", "999", ExternalStatus.UNKNOWN),
            ("INC0000001", "x", ExternalStatus.UNKNOWN),
            ("INC0000001", "9" * 400, ExternalStatus.UNKNOWN),
        ],
    )
    def test_a_servicenow_state_is_translated_per_table(
        self, number: str, state: str, expected: ExternalStatus
    ) -> None:
        assert servicenow_status(number, state) is expected


class TestFreeUrls:
    def test_a_link_is_kept_exactly_as_written(self) -> None:
        raw = "https://wiki.test/pages/42?view=full"

        assert parse_reference(ExternalKind.URL, f"  {raw} ", web_base=None) == ParsedReference(
            raw, raw
        )

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "ftp://wiki.test/a",
            "javascript:alert(1)",
            "https://",
            "https://user:pw@wiki.test/a",
            "https://wiki.test/é",
            "https://wiki.test/a b",
            "https://wiki.test/a\x00b",
            "https://wiki.test/" + "a" * 2048,
        ],
    )
    def test_anything_that_is_not_a_plain_web_address_is_refused(self, raw: str) -> None:
        with pytest.raises(InvalidExternalReferenceError):
            parse_reference(ExternalKind.URL, raw, web_base=None)


class TestTitlesFromThirdParties:
    def test_markup_is_kept_as_text_and_never_interpreted_here(self) -> None:
        assert clean_title("<script>alert(1)</script>") == "<script>alert(1)</script>"

    def test_control_characters_are_replaced_because_postgresql_cannot_store_a_nul(self) -> None:
        assert clean_title("Fix\x00 the\nrounding\t bug") == "Fix the rounding bug"

    def test_invisible_formatting_characters_that_reorder_text_are_dropped(self) -> None:
        assert clean_title("‮evil") == "evil"

    def test_a_title_is_bounded(self) -> None:
        assert len(clean_title("x" * 5000)) == MAX_TITLE_LENGTH

    def test_a_snapshot_cleans_the_title_it_is_built_with(self) -> None:
        assert ExternalSnapshot.of("a\x00b", ExternalStatus.OPEN).title == "a b"


class TestFreshness:
    def _reference(
        self, *, synced_at: datetime | None, kind: ExternalKind = GITHUB
    ) -> ExternalReference:
        reference = ExternalReference.create(
            element_id=uuid4(),
            kind=kind,
            target="" if kind is ExternalKind.URL else "main",
            external_id="acme/billing#12",
            url="https://github.test/acme/billing/issues/12",
            snapshot=None,
            created_by="editor",
            now=NOW,
        )
        if synced_at is None:
            return reference
        return reference.with_snapshot(ExternalSnapshot.of("t", ExternalStatus.OPEN), now=synced_at)

    def test_a_reference_never_resolved_is_stale(self) -> None:
        assert self._reference(synced_at=None).is_stale(NOW) is True

    def test_it_is_stale_after_the_threshold_and_not_before(self) -> None:
        assert self._reference(synced_at=NOW - STALE_AFTER).is_stale(NOW) is False
        assert self._reference(synced_at=NOW - STALE_AFTER - timedelta(seconds=1)).is_stale(NOW)

    def test_a_free_link_has_nothing_to_refresh_so_it_is_never_stale(self) -> None:
        assert self._reference(synced_at=None, kind=ExternalKind.URL).is_stale(NOW) is False

    def test_a_snapshot_updates_the_cache_and_keeps_the_identity(self) -> None:
        before = self._reference(synced_at=None)
        after = before.with_snapshot(ExternalSnapshot.of("Done", ExternalStatus.CLOSED), now=NOW)

        assert (after.id, after.external_id, after.url) == (
            before.id,
            before.external_id,
            before.url,
        )
        assert (after.title, after.status, after.synced_at) == ("Done", ExternalStatus.CLOSED, NOW)
