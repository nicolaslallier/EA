"""Importing an exchange file: the collision rule, the report, dry-run, and the replay of rows."""

from __future__ import annotations

import hashlib
import logging
import unicodedata
from dataclasses import replace
from uuid import UUID

import pytest

from ea.domain.approval import ApprovalState
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.archimate import permits
from ea.domain.errors import (
    DuplicateElementError,
    ExchangeFileTooLargeError,
    ImportInProgressError,
    InvalidExchangeFileError,
    NotAuthenticatedError,
    NotAuthorisedError,
    TooManyRefusedRowsError,
    WriteConflictError,
)
from ea.domain.exchange import (
    SOURCE_ID,
    Category,
    ConflictPolicy,
    ExchangeFormat,
    ImportContent,
    ImportOptions,
    Severity,
)
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.exchanges import ExchangeService
from tests.conftest import InMemoryExchange, InMemoryRepository, a_reader, an_approver
from tests.exchange_files import DATA, el, exchange_xml, rel

pytestmark = pytest.mark.asyncio
REAL = ImportOptions(dry_run=False)


def xml(data: bytes) -> ImportContent:
    return ImportContent(ExchangeFormat.ARCHIMATE_XML, data)


def host(identifier: str, name: str, address: str = "10.0.0.5") -> str:
    """A node answering on `address`: two of them clash in the database, not in the file."""
    return el(
        identifier,
        "Node",
        name,
        '<properties><property propertyDefinitionRef="ip"><value>' + address + "</value>"
        "</property></properties>",
    )


IP_DEFINITION = (
    '<propertyDefinitions><propertyDefinition identifier="ip" type="string">'
    "<name>ip_address</name></propertyDefinition></propertyDefinitions>"
)


def refusing_on_write(
    monkeypatch: pytest.MonkeyPatch, service: ArchitectureService, *names: str
) -> None:
    """The database refuses these names at write time, as a concurrent duplicate would.

    Nothing in the file says so, so the plan cannot know: this is what spends the budget.
    """
    real = service.create_element

    async def refuse(**kwargs: object) -> object:
        if kwargs["name"] in names:
            raise DuplicateElementError("somebody created it meanwhile")
        return await real(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(service, "create_element", refuse)


def names_of(repository: InMemoryRepository) -> dict[UUID, str]:
    return {uid: e.name for uid, e in repository.elements.items()}


class TestTheDefaultIsToWriteNothing:
    async def test_an_import_with_default_options_is_a_dry_run(
        self, exchange_service: ExchangeService, repository: InMemoryRepository
    ) -> None:
        content = xml((DATA / "minimal-model.xml").read_bytes())

        report = await exchange_service.import_exchange(content, ImportOptions())

        assert report.dry_run and not report.applied
        assert report.counts[Category.ELEMENTS].created == 3
        assert report.counts[Category.ELEMENTS].ignored == 1  # the junction
        assert repository.elements == {}
        assert repository.audit_log.events == []

    async def test_a_real_import_writes_and_reports_the_same_counts(
        self, exchange_service: ExchangeService, repository: InMemoryRepository
    ) -> None:
        content = xml((DATA / "minimal-model.xml").read_bytes())

        simulated = await exchange_service.import_exchange(content, ImportOptions())
        real = await exchange_service.import_exchange(content, REAL)

        assert real.applied and not real.dry_run
        assert real.counts == simulated.counts
        assert real.issues == simulated.issues
        assert real.sha256 == simulated.sha256 == hashlib.sha256(content.main).hexdigest()
        stored = {e.name: e for e in repository.elements.values()}
        assert stored["Shop"].properties == {"owner": "retail", SOURCE_ID: "id-app"}
        assert stored["Customer"].documentation == "Buys things"


class TestTheCollisionRule:
    async def test_an_existing_element_is_reused_and_only_a_blank_description_is_filled(
        self, exchange_service: ExchangeService, service: ArchitectureService
    ) -> None:
        kept = await service.create_element(
            element_type=E.NODE, name="srv", description="mine", properties={"tier": "gold"}
        )
        empty = await service.create_element(element_type=E.NODE, name="bare")
        described = (
            '<properties><property propertyDefinitionRef="d"><value>from the file</value>'
            "</property></properties>"
        )
        content = xml(
            exchange_xml(
                el("a", "Node", "srv", "<documentation>x</documentation>")
                + el("b", "Node", "bare", described),
                extra=(
                    '<propertyDefinitions><propertyDefinition identifier="d" type="string">'
                    "<name>ea.description</name></propertyDefinition></propertyDefinitions>"
                ),
            )
        )

        report = await exchange_service.import_exchange(content, REAL)

        assert report.counts[Category.ELEMENTS].reused == 2
        assert report.counts[Category.ELEMENTS].created == 0
        after_kept = await service.get_element(kept.id)
        after_empty = await service.get_element(empty.id)
        assert after_kept.description == "mine"
        assert dict(after_kept.properties) == {"tier": "gold", SOURCE_ID: "a"}
        assert after_empty.description == "from the file"

    async def test_on_conflict_fail_makes_an_existing_element_a_rejected_row(
        self, exchange_service: ExchangeService, service: ArchitectureService
    ) -> None:
        await service.create_element(element_type=E.NODE, name="srv")

        report = await exchange_service.import_exchange(
            xml(exchange_xml(el("a", "Node", "srv"))),
            ImportOptions(dry_run=False, on_conflict=ConflictPolicy.FAIL),
        )

        assert report.counts[Category.ELEMENTS].rejected == 1
        assert report.issues[0].code == "already_exists"
        assert report.issues[0].source_id == "a"

    async def test_the_same_type_and_name_under_two_identifiers_is_merged_with_a_warning(
        self, exchange_service: ExchangeService, repository: InMemoryRepository
    ) -> None:
        content = xml(exchange_xml(el("a", "Node", "srv") + el("b", "Node", "srv")))

        report = await exchange_service.import_exchange(content, REAL)

        assert len(repository.elements) == 1
        assert report.counts[Category.ELEMENTS].created == 1
        assert report.counts[Category.ELEMENTS].reused == 1
        assert [i.code for i in report.issues] == ["duplicate_in_file"]


class TestRowsThatAreRefused:
    async def test_a_blank_or_overlong_name_is_a_row_not_a_failure(
        self,
        exchange_service: ExchangeService,
        repository: InMemoryRepository,
        exchange_store: InMemoryExchange,
    ) -> None:
        content = xml(
            exchange_xml(el("a", "Node", " ") + el("b", "Node", "x" * 201) + el("c", "Node", "ok"))
        )

        report = await exchange_service.import_exchange(content, REAL)

        assert [i.code for i in report.issues] == ["blank_name", "name_too_long"]
        assert report.counts[Category.ELEMENTS].created == 1
        assert [e.name for e in repository.elements.values()] == ["ok"]
        assert exchange_store.applied == 1  # the two refusals never reached the database

    async def test_a_duplicate_ip_address_is_a_refused_row(
        self, exchange_service: ExchangeService
    ) -> None:
        content = xml(exchange_xml(host("a", "one") + host("b", "two"), extra=IP_DEFINITION))

        report = await exchange_service.import_exchange(content, REAL)

        assert report.counts[Category.ELEMENTS].created == 1
        assert report.counts[Category.ELEMENTS].rejected == 1
        assert report.issues[0].code == "address_taken"

    async def test_a_malformed_ip_address_is_refused_without_reaching_the_database(
        self, exchange_service: ExchangeService, exchange_store: InMemoryExchange
    ) -> None:
        content = xml(exchange_xml(host("a", "one", "not-an-address"), extra=IP_DEFINITION))

        report = await exchange_service.import_exchange(content, REAL)

        assert report.counts[Category.ELEMENTS].rejected == 1
        assert exchange_store.applied == 0


class TestWhoMayImport:
    async def test_a_reader_may_not_even_simulate(self, exchange_service: ExchangeService) -> None:
        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await exchange_service.import_exchange(xml(exchange_xml()), ImportOptions())

    async def test_nobody_may_import(
        self, exchange_service: ExchangeService, nobody_calling: None
    ) -> None:
        with pytest.raises(NotAuthenticatedError):
            await exchange_service.import_exchange(xml(exchange_xml()), ImportOptions())


class TestRefusedFiles:
    async def test_a_file_over_the_ceiling_is_too_large(
        self, exchange_store: InMemoryExchange, service: ArchitectureService
    ) -> None:
        small = ExchangeService(exchange_store, service, max_bytes=10)

        with pytest.raises(ExchangeFileTooLargeError, match="10 bytes"):
            await small.import_exchange(xml(b"x" * 11), ImportOptions())

    async def test_a_file_exactly_at_the_ceiling_is_read(
        self, exchange_store: InMemoryExchange, service: ArchitectureService
    ) -> None:
        data = exchange_xml()
        exact = ExchangeService(exchange_store, service, max_bytes=len(data))

        report = await exact.import_exchange(xml(data), ImportOptions())

        assert report.dry_run

    async def test_an_unreadable_file_writes_nothing_and_raises(
        self, exchange_service: ExchangeService, repository: InMemoryRepository
    ) -> None:
        with pytest.raises(InvalidExchangeFileError):
            await exchange_service.import_exchange(xml(b"<model>"), REAL)

        assert repository.elements == {}


class TestTheLog:
    async def test_one_line_with_counts_and_the_digest_never_the_content(
        self, exchange_service: ExchangeService, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.clear()
        with caplog.at_level(logging.INFO):
            await exchange_service.import_exchange(
                xml(exchange_xml(el("a", "Node", "Secret"))), REAL
            )

        (line,) = [r for r in caplog.records if getattr(r, "action", None) == "imported"]
        assert line.dry_run is False and line.rows_created == 1  # type: ignore[attr-defined]
        assert line.actor == "editor-1"  # type: ignore[attr-defined]
        assert isinstance(line.duration_ms, int)  # type: ignore[attr-defined]
        assert len(line.sha256) == 64  # type: ignore[attr-defined]
        assert "Secret" not in caplog.text


class TestRelationships:
    async def test_a_relationship_is_created_with_its_identifier_as_source_id(
        self, exchange_service: ExchangeService, repository: InMemoryRepository
    ) -> None:
        report = await exchange_service.import_exchange(
            xml((DATA / "minimal-model.xml").read_bytes()), REAL
        )

        assert report.counts[Category.RELATIONSHIPS].created == 2  # the third touches the junction
        assert report.counts[Category.RELATIONSHIPS].rejected == 1
        served = next(r for r in repository.relationships.values() if r.name == "serves")
        assert served.properties[SOURCE_ID] == "id-r2"
        assert any(i.code == "end_rejected" and i.source_id == "id-r3" for i in report.issues)

    async def test_a_relationship_outside_the_matrix_is_reported_and_the_rest_is_written(
        self, exchange_service: ExchangeService, repository: InMemoryRepository
    ) -> None:
        assert not permits(R.SPECIALIZATION, E.BUSINESS_ACTOR, E.NODE)
        content = xml((DATA / "refused-relations.xml").read_bytes())

        report = await exchange_service.import_exchange(content, REAL)

        assert len(repository.elements) == 3
        assert [r.relationship_type for r in repository.relationships.values()] == [R.ASSOCIATION]
        refusal = next(i for i in report.issues if i.code == "relationship_not_allowed")
        assert refusal.severity is Severity.ERROR and refusal.source_id == "r-bad"
        assert report.applied

    async def test_strict_writes_nothing_when_any_row_is_refused(
        self, exchange_service: ExchangeService, repository: InMemoryRepository
    ) -> None:
        content = xml((DATA / "refused-relations.xml").read_bytes())

        report = await exchange_service.import_exchange(
            content, ImportOptions(dry_run=False, strict=True)
        )

        assert repository.elements == {} and repository.relationships == {}
        assert repository.audit_log.events == []
        assert not report.applied
        assert report.issues[-1].code == "strict_refused"

    async def test_a_relationship_to_an_unknown_identifier_is_a_rejected_row(
        self, exchange_service: ExchangeService
    ) -> None:
        content = xml(exchange_xml(el("a", "Node", "A"), rel("r", "Association", "a", "ghost")))

        report = await exchange_service.import_exchange(content, REAL)

        assert [i.code for i in report.issues] == ["unknown_end"]

    async def test_a_relationship_may_name_an_element_already_in_the_catalogue_by_source_id(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        repository: InMemoryRepository,
    ) -> None:
        await service.create_element(
            element_type=E.NODE, name="old", properties={SOURCE_ID: "legacy-1"}
        )
        content = xml(
            exchange_xml(el("n", "Node", "new"), rel("r", "Association", "n", "legacy-1"))
        )

        report = await exchange_service.import_exchange(content, REAL)

        assert report.counts[Category.RELATIONSHIPS].created == 1
        assert len(repository.relationships) == 1

    async def test_an_existing_link_is_reused_and_a_repeated_one_is_warned_about(
        self, exchange_service: ExchangeService
    ) -> None:
        content = xml(
            exchange_xml(
                el("a", "Node", "A") + el("b", "Node", "B"),
                rel("r1", "Association", "a", "b") + rel("r2", "Association", "a", "b"),
            )
        )

        first = await exchange_service.import_exchange(content, REAL)
        again = await exchange_service.import_exchange(content, REAL)

        assert first.counts[Category.RELATIONSHIPS].created == 1
        assert first.counts[Category.RELATIONSHIPS].reused == 1
        assert "duplicate_relationship" in [i.code for i in first.issues]
        assert again.counts[Category.RELATIONSHIPS].created == 0
        assert again.counts[Category.RELATIONSHIPS].reused == 2

    async def test_on_conflict_fail_refuses_an_existing_link(
        self, exchange_service: ExchangeService
    ) -> None:
        content = xml(
            exchange_xml(
                el("a", "Node", "A") + el("b", "Node", "B"), rel("r1", "Association", "a", "b")
            )
        )
        await exchange_service.import_exchange(content, REAL)

        report = await exchange_service.import_exchange(
            content, ImportOptions(dry_run=False, on_conflict=ConflictPolicy.FAIL)
        )

        assert report.counts[Category.RELATIONSHIPS].rejected == 1

    async def test_a_composition_loop_in_a_file_rejects_the_closing_row(
        self, exchange_service: ExchangeService, repository: InMemoryRepository
    ) -> None:
        content = xml(
            exchange_xml(
                el("a", "ApplicationComponent", "A") + el("b", "ApplicationComponent", "B"),
                rel("r1", "Composition", "a", "b") + rel("r2", "Composition", "b", "a"),
            )
        )

        report = await exchange_service.import_exchange(content, REAL)

        assert len(repository.relationships) == 1
        assert [i.code for i in report.issues] == ["cyclic_containment"]

    async def test_a_held_lock_is_a_409_and_writes_nothing(
        self,
        exchange_service: ExchangeService,
        exchange_store: InMemoryExchange,
        repository: InMemoryRepository,
    ) -> None:
        exchange_store.lock_held = True

        with pytest.raises(ImportInProgressError):
            await exchange_service.import_exchange(xml(exchange_xml(el("a", "Node", "A"))), REAL)

        assert repository.elements == {}

    async def test_the_report_keeps_complete_counts_when_its_lines_are_cut(
        self, exchange_service: ExchangeService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("ea.domain.exchange.MAX_REPORT_ISSUES", 2)
        rows = "".join(el(f"e{i}", "Node", " ") for i in range(5))

        report = await exchange_service.import_exchange(xml(exchange_xml(rows)), REAL)

        assert report.truncated and len(report.issues) == 2 and report.issue_count == 5
        assert report.counts[Category.ELEMENTS].rejected == 5


class TestWhatTheDomainJudgesAloneNeverSpendsTheBudget:
    async def test_five_hundred_links_the_matrix_forbids_are_all_listed_and_the_import_stands(
        self,
        exchange_service: ExchangeService,
        repository: InMemoryRepository,
        exchange_store: InMemoryExchange,
    ) -> None:
        actors = [el(f"a{i}", "BusinessActor", f"actor{i}") for i in range(25)]
        nodes = [el(f"n{i}", "Node", f"node{i}") for i in range(20)]
        links = [
            rel(f"r{a}-{n}", "Specialization", f"a{a}", f"n{n}")
            for a in range(25)
            for n in range(20)
        ]
        content = xml(exchange_xml("".join(actors + nodes), "".join(links)))

        report = await exchange_service.import_exchange(content, REAL)  # no TooManyRefusedRows

        assert report.applied
        assert report.counts[Category.RELATIONSHIPS].rejected == 500
        forbidden = [i for i in report.issues if i.code == "relationship_not_allowed"]
        assert len(forbidden) == 500
        assert len(repository.elements) == 45 and repository.relationships == {}
        assert exchange_store.applied == 45  # the 500 links were judged in memory

    async def test_more_than_a_hundred_refusals_by_the_database_reject_the_whole_file(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        repository: InMemoryRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        refusing_on_write(monkeypatch, service, *(f"host{i}" for i in range(1, 102)))
        rows = "".join(el(f"h{i}", "Node", f"host{i}") for i in range(102))  # 1 wins, 101 refused

        with pytest.raises(TooManyRefusedRowsError):
            await exchange_service.import_exchange(xml(exchange_xml(rows)), REAL)

        assert repository.elements == {} and repository.audit_log.events == []

    async def test_exactly_a_hundred_refusals_by_the_database_are_tolerated(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        repository: InMemoryRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        refusing_on_write(monkeypatch, service, *(f"host{i}" for i in range(1, 101)))
        rows = "".join(el(f"h{i}", "Node", f"host{i}") for i in range(101))

        report = await exchange_service.import_exchange(xml(exchange_xml(rows)), REAL)

        assert report.counts[Category.ELEMENTS].rejected == 100
        assert len(repository.elements) == 1


class TestWhatIsNotTheRowsFault:
    async def test_a_write_conflict_ends_the_import_it_is_never_a_refused_row(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        repository: InMemoryRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        real = service.create_element
        calls = {"n": 0}

        async def flaky(**kwargs: object) -> object:
            calls["n"] += 1
            if calls["n"] == 2:
                raise WriteConflictError("another change got in the way")
            return await real(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(service, "create_element", flaky)

        with pytest.raises(WriteConflictError):
            await exchange_service.import_exchange(
                xml(exchange_xml(el("a", "Node", "A") + el("b", "Node", "B"))), REAL
            )

        assert repository.elements == {}


class TestTheReplayOfRows:
    """A refusal rolls its batch back and replays the rows before it, under new ids."""

    async def test_links_follow_the_elements_created_again_by_the_replay(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        repository: InMemoryRepository,
        exchange_store: InMemoryExchange,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        refusing_on_write(monkeypatch, service, "D")
        content = xml(
            exchange_xml(
                el("a", "Node", "A")
                + el("a-again", "Node", "A")
                + el("b", "Node", "B")
                + el("c", "Node", "C")
                + el("d", "Node", "D"),  # refused by the database, after A, B and C were created
                rel("r1", "Association", "a", "b")
                + rel("r2", "Association", "c", "a-again")
                + rel("r3", "Association", "d", "a"),
            )
        )

        report = await exchange_service.import_exchange(content, REAL)

        assert exchange_store.replayed == 4  # A, A-again, B and C, applied a second time
        names = names_of(repository)
        assert sorted(names.values()) == ["A", "B", "C"]
        links = {
            (names[x.source_id], names[x.target_id]) for x in repository.relationships.values()
        }
        assert links == {("A", "B"), ("C", "A")}
        counts = report.counts
        assert counts[Category.ELEMENTS].created == 3
        assert counts[Category.ELEMENTS].reused == 1
        assert counts[Category.ELEMENTS].rejected == 1
        assert counts[Category.RELATIONSHIPS].created == 2
        assert counts[Category.RELATIONSHIPS].rejected == 1  # r3: its source was refused
        # what the first pass said is not said twice
        assert [i.code for i in report.issues].count("duplicate_in_file") == 1

    async def test_a_completion_made_before_the_refusal_is_made_again_not_lost_or_doubled(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        repository: InMemoryRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        existing = await service.create_element(element_type=E.NODE, name="old")
        events_before = len(repository.audit_log.events)
        refusing_on_write(monkeypatch, service, "D")
        content = xml(
            exchange_xml(el("o", "Node", "old") + el("c", "Node", "C") + el("d", "Node", "D"))
        )

        await exchange_service.import_exchange(content, REAL)

        stored = repository.elements[existing.id]
        assert stored.properties[SOURCE_ID] == "o"
        assert stored.revision == existing.revision + 1  # completed once
        # one update of "old" and one creation of "C": nothing orphaned by the rollback
        assert len(repository.audit_log.events) - events_before == 2

    async def test_rows_of_an_earlier_batch_keep_their_ids_through_a_later_replay(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        repository: InMemoryRepository,
        exchange_store: InMemoryExchange,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        refusing_on_write(monkeypatch, service, "E")
        exchange_store.batch_size = 2
        content = xml(
            exchange_xml(
                el("a", "Node", "A")
                + el("b", "Node", "B")  # batch 1: complete
                + el("d", "Node", "D")
                + el("e", "Node", "E"),  # batch 2: E is refused, D is applied again
                rel("r1", "Association", "a", "b") + rel("r2", "Association", "d", "a"),
            )
        )

        report = await exchange_service.import_exchange(content, REAL)

        names = names_of(repository)
        links = {
            (names[x.source_id], names[x.target_id]) for x in repository.relationships.values()
        }
        assert links == {("A", "B"), ("D", "A")}
        assert report.counts[Category.ELEMENTS].rejected == 1
        assert exchange_store.replayed == 1

    async def test_a_dry_run_replays_just_the_same(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        repository: InMemoryRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        refusing_on_write(monkeypatch, service, "D")
        content = xml(
            exchange_xml(
                el("a", "Node", "A") + el("c", "Node", "C") + el("d", "Node", "D"),
                rel("r", "Association", "a", "c"),
            )
        )

        simulated = await exchange_service.import_exchange(content, ImportOptions())
        real = await exchange_service.import_exchange(content, REAL)

        assert simulated.counts == real.counts and simulated.issues == real.issues
        assert real.counts[Category.RELATIONSHIPS].created == 1


class TestReview:
    def approve(self, repository: InMemoryRepository, element_id: UUID, **state: object) -> None:
        stored = repository.elements[element_id]
        repository.elements[element_id] = replace(
            stored,
            approval_state=ApprovalState.APPROVED,
            **state,  # type: ignore[arg-type]
        )

    async def test_completing_an_approved_element_is_a_warning_not_a_failure_for_an_editor(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        repository: InMemoryRepository,
    ) -> None:
        approved = await service.create_element(element_type=E.NODE, name="srv")
        self.approve(repository, approved.id)
        content = xml(exchange_xml(el("s", "Node", "srv")))

        report = await exchange_service.import_exchange(content, REAL)

        assert report.counts[Category.ELEMENTS].reused == 1
        assert [i.code for i in report.issues] == ["completion_not_applied"]
        stored = repository.elements[approved.id]
        assert (
            stored.approval_state is ApprovalState.APPROVED and SOURCE_ID not in stored.properties
        )

    async def test_an_approver_importing_over_ten_approved_elements_changes_none_of_them(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        repository: InMemoryRepository,
    ) -> None:
        ids = []
        for number in range(10):
            element = await service.create_element(element_type=E.NODE, name=f"srv{number}")
            self.approve(repository, element.id, approved_by_sub="approver-9")
            ids.append(element.id)
        before = {uid: repository.elements[uid] for uid in ids}
        events = len(repository.audit_log.events)
        rows = "".join(el(f"s{n}", "Node", f"srv{n}") for n in range(10))

        with acting_as(an_approver()):  # a bulk import never reopens a review, for any role
            report = await exchange_service.import_exchange(xml(exchange_xml(rows)), REAL)

        assert [i.code for i in report.issues] == ["completion_not_applied"] * 10
        assert report.counts[Category.ELEMENTS].reused == 10
        assert {uid: repository.elements[uid] for uid in ids} == before  # not even a revision
        assert len(repository.audit_log.events) == events

    async def test_linking_an_approved_element_reopens_it_and_a_dry_run_leaves_it_approved(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        repository: InMemoryRepository,
    ) -> None:
        approved = await service.create_element(
            element_type=E.NODE, name="srv", properties={SOURCE_ID: "srv-1"}
        )
        self.approve(repository, approved.id)
        content = xml(exchange_xml(el("n", "Node", "new"), rel("r", "Association", "n", "srv-1")))

        simulated = await exchange_service.import_exchange(content, ImportOptions())
        assert repository.elements[approved.id].approval_state is ApprovalState.APPROVED
        real = await exchange_service.import_exchange(content, REAL)

        assert [i.code for i in real.issues] == ["approved_element_reopened"]
        assert [i.code for i in simulated.issues] == ["approved_element_reopened"]
        assert repository.elements[approved.id].approval_state is ApprovalState.IN_REVIEW

    async def test_a_link_to_a_locked_element_is_a_refused_row_the_rest_stands(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        repository: InMemoryRepository,
    ) -> None:
        locked = await service.create_element(
            element_type=E.NODE, name="srv", properties={SOURCE_ID: "srv-1"}
        )
        repository.elements[locked.id] = replace(locked, locked=True)
        content = xml(exchange_xml(el("n", "Node", "new"), rel("r", "Association", "n", "srv-1")))

        report = await exchange_service.import_exchange(content, REAL)

        assert report.counts[Category.ELEMENTS].created == 1
        assert report.counts[Category.RELATIONSHIPS].rejected == 1
        assert [i.code for i in report.issues] == ["element_locked"]
        assert repository.relationships == {}


class TestTheLocksTakenBeforeTheFirstWrite:
    async def test_the_existing_elements_the_import_will_touch_are_locked_in_id_order(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        exchange_store: InMemoryExchange,
    ) -> None:
        one = await service.create_element(element_type=E.NODE, name="one")
        two = await service.create_element(element_type=E.NODE, name="two")
        await service.create_element(element_type=E.NODE, name="untouched")
        content = xml(
            exchange_xml(
                el("1", "Node", "one") + el("2", "Node", "two") + el("3", "Node", "three"),
                rel("r", "Association", "3", "1"),
            )
        )

        await exchange_service.import_exchange(content, REAL)

        assert exchange_store.locked == sorted([one.id, two.id])


class TestCompletionIsDecidedOnTheRowAsItIsNow:
    """The catalogue is read once, at the start; the decision to fill a blank is not."""

    def after_the_catalogue_is_read(
        self,
        monkeypatch: pytest.MonkeyPatch,
        exchange_store: InMemoryExchange,
        change: object,
    ) -> None:
        original = exchange_store.known_elements

        async def stale() -> tuple[object, ...]:
            loaded = await original()
            change()  # type: ignore[operator] - another person writes, the copy above is stale
            return loaded

        monkeypatch.setattr(exchange_store, "known_elements", stale)

    async def test_a_description_written_meanwhile_is_not_overwritten(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        repository: InMemoryRepository,
        exchange_store: InMemoryExchange,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        existing = await service.create_element(element_type=E.NODE, name="srv")

        def theirs() -> None:
            repository.elements[existing.id] = replace(
                repository.elements[existing.id], description="theirs"
            )

        self.after_the_catalogue_is_read(monkeypatch, exchange_store, theirs)
        described = (
            '<properties><property propertyDefinitionRef="d"><value>from the file</value>'
            "</property></properties>"
        )
        data = exchange_xml(
            el("s", "Node", "srv", described),
            extra='<propertyDefinitions><propertyDefinition identifier="d" type="string">'
            "<name>ea.description</name></propertyDefinition></propertyDefinitions>",
        )

        await exchange_service.import_exchange(xml(data), REAL)

        stored = repository.elements[existing.id]
        assert stored.description == "theirs"
        assert stored.properties[SOURCE_ID] == "s"  # the blank that was still blank is filled

    async def test_a_source_id_written_meanwhile_is_not_overwritten_and_nothing_is_written(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        repository: InMemoryRepository,
        exchange_store: InMemoryExchange,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        existing = await service.create_element(element_type=E.NODE, name="srv")

        def theirs() -> None:
            stored = repository.elements[existing.id]
            repository.elements[existing.id] = replace(stored, properties={SOURCE_ID: "theirs"})

        self.after_the_catalogue_is_read(monkeypatch, exchange_store, theirs)
        events = len(repository.audit_log.events)
        before = repository.elements[existing.id].revision

        report = await exchange_service.import_exchange(
            xml(exchange_xml(el("s", "Node", "srv"))), REAL
        )

        assert report.counts[Category.ELEMENTS].reused == 1
        assert repository.elements[existing.id].properties[SOURCE_ID] == "theirs"
        assert repository.elements[existing.id].revision == before
        assert len(repository.audit_log.events) == events  # nothing to fill: no event, no revision

    async def test_an_element_approved_meanwhile_refuses_the_completion_and_the_import_goes_on(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        repository: InMemoryRepository,
        exchange_store: InMemoryExchange,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        existing = await service.create_element(element_type=E.NODE, name="srv")

        def approved() -> None:
            stored = repository.elements[existing.id]
            repository.elements[existing.id] = replace(
                stored, approval_state=ApprovalState.APPROVED
            )

        self.after_the_catalogue_is_read(monkeypatch, exchange_store, approved)

        report = await exchange_service.import_exchange(
            xml(exchange_xml(el("s", "Node", "srv") + el("n", "Node", "new"))), REAL
        )

        assert [i.code for i in report.issues] == ["completion_not_applied"]
        assert report.counts[Category.ELEMENTS].reused == 1
        assert report.counts[Category.ELEMENTS].created == 1
        assert SOURCE_ID not in repository.elements[existing.id].properties

    async def test_an_element_deleted_meanwhile_is_a_refused_row_not_a_failure(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        repository: InMemoryRepository,
        exchange_store: InMemoryExchange,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        existing = await service.create_element(element_type=E.NODE, name="srv")
        self.after_the_catalogue_is_read(
            monkeypatch, exchange_store, lambda: repository.elements.pop(existing.id)
        )

        report = await exchange_service.import_exchange(
            xml(exchange_xml(el("s", "Node", "srv") + el("n", "Node", "new"))), REAL
        )

        assert [i.code for i in report.issues] == ["element_gone"]
        assert report.counts[Category.ELEMENTS].rejected == 1
        assert report.counts[Category.ELEMENTS].created == 1


class TestAHostileFile:
    """Every value of the file is judged by the domain before a service sees it."""

    def definitions(self, **keys: str) -> str:
        rows = "".join(
            f'<propertyDefinition identifier="{ref}" type="string"><name>{name}</name>'
            "</propertyDefinition>"
            for ref, name in keys.items()
        )
        return f"<propertyDefinitions>{rows}</propertyDefinitions>"

    def prop(self, ref: str, value: str) -> str:
        return (
            f'<properties><property propertyDefinitionRef="{ref}"><value>{value}</value>'
            "</property></properties>"
        )

    async def test_each_hostile_row_is_rejected_by_name_and_nothing_invalid_is_written(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        repository: InMemoryRepository,
        exchange_store: InMemoryExchange,
    ) -> None:
        await service.create_element(
            element_type=E.NODE, name="taken", properties={"ip_address": "10.9.9.9"}
        )
        rows = (
            el("long", "Node", "n" * 10_000)
            + el("cidr", "Node", "badcidr", self.prop("c", "10.0.0.0/99"))
            + el("dup", "Node", "dup", self.prop("i", "10.9.9.9"))
            + el("kind", "Spaceship", "ship")
            + el("tab", "Node", "tab&#9;name")
            + el("good", "Node", "good", self.prop("k", "v"))
            + '<element identifier="extra" xsi:type="Node"'
            ' id="00000000-0000-0000-0000-000000000001"'
            ' uuid="00000000-0000-0000-0000-000000000002"><name>extra</name></element>'
        )
        data = exchange_xml(rows, extra=self.definitions(c="cidr", i="ip_address", k="Owner\nname"))

        report = await exchange_service.import_exchange(xml(data), REAL)

        by_row = {i.source_id: i.code for i in report.issues if i.severity is Severity.ERROR}
        assert by_row == {
            "long": "name_too_long",
            "cidr": "invalid_ipam_property",
            "dup": "address_taken",
            "kind": "unknown_type",
            "tab": "control_character",
        }
        assert "invalid_property_key" in [i.code for i in report.issues]  # the key with a newline
        stored = {e.name: e for e in repository.elements.values()}
        assert sorted(stored) == ["extra", "good", "taken"]
        assert (
            "Owner\nname" not in stored["good"].properties and "k" not in stored["good"].properties
        )
        assert str(stored["extra"].id).startswith("00000000-0000-0000-0000-00000000000") is False
        assert exchange_store.applied == 2  # good and extra: the duplicate never got there

    async def test_a_key_with_a_trailing_newline_is_not_trimmed_into_a_valid_one(
        self, exchange_service: ExchangeService, repository: InMemoryRepository
    ) -> None:
        data = exchange_xml(
            el("a", "Node", "A", self.prop("k", "v")), extra=self.definitions(k="owner\n")
        )

        report = await exchange_service.import_exchange(xml(data), REAL)

        assert "invalid_property_key" in [i.code for i in report.issues]
        (stored,) = repository.elements.values()
        assert set(stored.properties) == {SOURCE_ID}

    async def test_a_nul_in_the_file_is_not_a_500_it_is_an_unreadable_file(
        self, exchange_service: ExchangeService, repository: InMemoryRepository
    ) -> None:
        data = exchange_xml(el("a", "Node", "A\x00B"))

        with pytest.raises(InvalidExchangeFileError):
            await exchange_service.import_exchange(xml(data), REAL)

        assert repository.elements == {}

    async def test_the_services_receive_only_what_the_domain_handed_back(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        seen: list[dict[str, object]] = []
        real = service.create_element

        async def spy(**kwargs: object) -> object:
            seen.append(kwargs)
            return await real(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(service, "create_element", spy)
        data = exchange_xml(
            el("a", "Node", "  padded  ", self.prop("i", "2001:DB8::1")),
            extra=self.definitions(i="ip_address"),
        )

        await exchange_service.import_exchange(xml(data), REAL)

        (call,) = seen
        assert call["name"] == "padded"  # already clean when it arrives
        assert call["properties"] == {"ip_address": "2001:db8::1", "vrf": "default", SOURCE_ID: "a"}


class TestAddressesAndPrefixesClashingInsideTheFile:
    """Judged in the plan, so they never spend the budget of refusals by the database."""

    def hosts(self, count: int, ip: str, *, vrf: str | None = None) -> str:
        def one(number: int) -> str:
            props = f'<property propertyDefinitionRef="ip"><value>{ip}</value></property>'
            if vrf is not None:
                props += f'<property propertyDefinitionRef="vrf"><value>{vrf}</value></property>'
            return el(f"h{number}", "Node", f"host{number}", f"<properties>{props}</properties>")

        return "".join(one(n) for n in range(count))

    DEFINITIONS = (
        '<propertyDefinitions><propertyDefinition identifier="ip" type="string">'
        "<name>ip_address</name></propertyDefinition>"
        '<propertyDefinition identifier="vrf" type="string"><name>vrf</name>'
        '</propertyDefinition><propertyDefinition identifier="net" type="string">'
        "<name>cidr</name></propertyDefinition></propertyDefinitions>"
    )

    async def test_a_hundred_and_fifty_hosts_on_one_address_do_not_reject_the_file(
        self, exchange_service: ExchangeService, exchange_store: InMemoryExchange
    ) -> None:
        data = exchange_xml(self.hosts(150, "10.0.0.5"), extra=self.DEFINITIONS)

        report = await exchange_service.import_exchange(xml(data), REAL)

        assert report.counts[Category.ELEMENTS].created == 1
        assert report.counts[Category.ELEMENTS].rejected == 149
        assert {i.code for i in report.issues} == {"address_taken"}
        assert exchange_store.applied == 1  # the 149 were judged without the database

    async def test_two_spellings_of_one_ipv6_address_are_one_address(
        self, exchange_service: ExchangeService
    ) -> None:
        rows = self.hosts(1, "::1") + el(
            "h9",
            "Node",
            "other",
            '<properties><property propertyDefinitionRef="ip"><value>0:0:0:0:0:0:0:1</value>'
            "</property></properties>",
        )

        report = await exchange_service.import_exchange(
            xml(exchange_xml(rows, extra=self.DEFINITIONS)), REAL
        )

        assert [i.code for i in report.issues] == ["address_taken"]

    async def test_the_same_address_in_two_vrfs_is_two_addresses(
        self, exchange_service: ExchangeService, repository: InMemoryRepository
    ) -> None:
        rows = self.hosts(1, "10.0.0.5", vrf="a") + el(
            "h9",
            "Node",
            "other",
            '<properties><property propertyDefinitionRef="ip"><value>10.0.0.5</value></property>'
            '<property propertyDefinitionRef="vrf"><value>b</value></property></properties>',
        )

        report = await exchange_service.import_exchange(
            xml(exchange_xml(rows, extra=self.DEFINITIONS)), REAL
        )

        assert report.issues == () and len(repository.elements) == 2

    async def test_an_address_already_in_the_catalogue_is_refused_in_the_plan(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        exchange_store: InMemoryExchange,
    ) -> None:
        await service.create_element(
            element_type=E.NODE, name="taken", properties={"ip_address": "10.0.0.5"}
        )

        report = await exchange_service.import_exchange(
            xml(exchange_xml(self.hosts(3, "10.0.0.5"), extra=self.DEFINITIONS)), REAL
        )

        assert report.counts[Category.ELEMENTS].rejected == 3
        assert exchange_store.applied == 0

    async def test_a_merged_element_writes_no_address_so_it_holds_none(
        self, exchange_service: ExchangeService, service: ArchitectureService
    ) -> None:
        await service.create_element(element_type=E.NODE, name="host0")  # reused, never written
        rows = self.hosts(1, "10.0.0.5") + el(
            "h9",
            "Node",
            "newcomer",
            '<properties><property propertyDefinitionRef="ip"><value>10.0.0.5</value>'
            "</property></properties>",
        )

        report = await exchange_service.import_exchange(
            xml(exchange_xml(rows, extra=self.DEFINITIONS)), REAL
        )

        assert report.counts[Category.ELEMENTS].created == 1 and report.issues == ()

    async def test_two_networks_with_one_prefix_clash_in_the_plan(
        self, exchange_service: ExchangeService, exchange_store: InMemoryExchange
    ) -> None:
        def network(number: int) -> str:
            return el(
                f"n{number}",
                "CommunicationNetwork",
                f"lan{number}",
                '<properties><property propertyDefinitionRef="net"><value>10.1.0.0/24</value>'
                "</property></properties>",
            )

        report = await exchange_service.import_exchange(
            xml(exchange_xml(network(1) + network(2), extra=self.DEFINITIONS)), REAL
        )

        assert [i.code for i in report.issues] == ["cidr_taken"]
        assert exchange_store.applied == 1


class TestTheReportNeverEchoesTheFile:
    async def test_a_huge_identifier_and_forged_lines_stay_small_and_inert(
        self, exchange_service: ExchangeService
    ) -> None:
        huge = "A" * 900_000  # under the single-token bound, far over any identifier
        forged = "id&#13;&#10;[ERROR] forged log line&#x202E;"
        rows = (
            el(huge, "Node", "first")
            + el(huge, "Node", "second")
            + el(huge, "Node", "third")
            + el(forged, "Node", "bad&#13;&#10;[ERROR] fake log line&#x202E;")
        )

        report = await exchange_service.import_exchange(xml(exchange_xml(rows)), REAL)

        assert report.issue_count >= 3
        sizes = 0
        for issue in report.issues:
            for text in (issue.message, issue.source_id or "", issue.code, issue.path or ""):
                sizes += len(text)
                assert len(text) <= 201
                assert not any(unicodedata.category(c) in ("Cc", "Cf") for c in text), repr(text)
        assert sizes < 4_000  # a fixed bound, whatever the file's size


class TestWhatTheReplayMustRemember:
    async def test_links_applied_before_a_refused_link_are_applied_again_not_taken_for_existing(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        repository: InMemoryRepository,
    ) -> None:
        # The elements are in the catalogue already: their ids do not change on the replay,
        # so a link key remembered from the first pass would be found again.
        for name in "ABCD":
            await service.create_element(element_type=E.APPLICATION_COMPONENT, name=name)
        letters = "".join(el(name.lower(), "ApplicationComponent", name) for name in "ABCD")
        chain = (
            rel("r1", "Composition", "a", "b")
            + rel("r2", "Composition", "b", "c")
            + rel("r3", "Composition", "c", "d")
            + rel("r4", "Composition", "d", "a")  # closes the loop: refused after the three
        )

        report = await exchange_service.import_exchange(xml(exchange_xml(letters, chain)), REAL)

        assert [i.code for i in report.issues] == ["cyclic_containment"]
        assert report.counts[Category.RELATIONSHIPS].created == 3
        assert report.counts[Category.RELATIONSHIPS].reused == 0
        names = names_of(repository)
        links = {
            (names[x.source_id], names[x.target_id]) for x in repository.relationships.values()
        }
        assert links == {("A", "B"), ("B", "C"), ("C", "D")}  # in the graph, not just the report

    async def test_a_link_naming_a_refused_element_never_lands_on_another_with_that_source_id(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        repository: InMemoryRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await service.create_element(
            element_type=E.NODE, name="stranger", properties={SOURCE_ID: "x"}
        )
        refusing_on_write(monkeypatch, service, "refused")
        data = exchange_xml(
            el("ok", "Node", "ok") + el("x", "Node", "refused"),  # "x" is also the stranger's
            rel("r", "Association", "ok", "x"),
        )

        report = await exchange_service.import_exchange(xml(data), REAL)

        assert repository.relationships == {}
        assert [i.code for i in report.issues] == ["duplicate_element", "end_rejected"]

    async def test_an_element_reopened_before_a_refusal_is_reopened_and_reported_after_it(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        repository: InMemoryRepository,
    ) -> None:
        approved = await service.create_element(
            element_type=E.APPLICATION_COMPONENT, name="srv", properties={SOURCE_ID: "srv-1"}
        )
        repository.elements[approved.id] = replace(
            repository.elements[approved.id], approval_state=ApprovalState.APPROVED
        )
        data = exchange_xml(
            el("n", "ApplicationComponent", "N") + el("m", "ApplicationComponent", "M"),
            rel("r1", "Association", "n", "srv-1")  # reopens "srv"
            + rel("r2", "Composition", "n", "m")
            + rel("r3", "Composition", "m", "n"),  # refused: the batch is rolled back, r1 replayed
        )

        report = await exchange_service.import_exchange(xml(data), REAL)

        reopened = [i for i in report.issues if i.code == "approved_element_reopened"]
        assert [i.source_id for i in reopened] == ["r1"]  # said once, and after the replay
        assert repository.elements[approved.id].approval_state is ApprovalState.IN_REVIEW


def test_the_bounds_have_the_values_the_plan_states() -> None:
    from ea.domain.exchange import MAX_EXCHANGE_BYTES, MAX_EXCHANGE_ITEMS, MAX_NESTING_DEPTH

    assert MAX_EXCHANGE_ITEMS == 50_000
    assert MAX_EXCHANGE_BYTES == 20 * 1024 * 1024
    assert MAX_NESTING_DEPTH == 250  # 200 levels of organizations, and the frame around them


class TestARowRefusedOnlyTheSecondTime:
    async def test_the_import_is_condemned_not_continued(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        repository: InMemoryRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        real = service.create_element
        calls: dict[str, int] = {}

        async def create(**kwargs: object) -> object:
            name = str(kwargs["name"])
            calls[name] = calls.get(name, 0) + 1
            if name == "B" or (name == "A" and calls[name] > 1):  # A is refused on its replay
                raise DuplicateElementError("not reproducible")
            return await real(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(service, "create_element", create)

        with pytest.raises(WriteConflictError):
            await exchange_service.import_exchange(
                xml(exchange_xml(el("a", "Node", "A") + el("b", "Node", "B"))), REAL
            )

        assert repository.elements == {}
