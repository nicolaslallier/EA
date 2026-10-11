"""Importing views: boxes only, valid coordinates only, never an element made by a view."""

from __future__ import annotations

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.audit import AuditAction
from ea.domain.diagrams import DiagramNode
from ea.domain.errors import DuplicateDiagramError, TooManyRefusedRowsError
from ea.domain.exchange import (
    SOURCE_ID,
    Category,
    ExchangeFormat,
    ImportContent,
    ImportOptions,
)
from ea.services.architecture import ArchitectureService
from ea.services.diagrams import DiagramService
from ea.services.exchanges import ExchangeService
from tests.conftest import FIXED_NOW, InMemoryDiagrams, InMemoryExchange, InMemoryRepository
from tests.exchange_files import DATA, el, exchange_xml

pytestmark = pytest.mark.asyncio
REAL = ImportOptions(dry_run=False)


@pytest.fixture
def importer(
    exchange_store: InMemoryExchange, service: ArchitectureService, diagram_service: DiagramService
) -> ExchangeService:
    return ExchangeService(
        exchange_store, service, diagrams=diagram_service, clock=lambda: FIXED_NOW
    )


def xml(data: bytes) -> ImportContent:
    return ImportContent(ExchangeFormat.ARCHIMATE_XML, data)


def a_file(*views: str, elements: str = "") -> ImportContent:
    return xml(
        exchange_xml(
            elements or el("a", "Node", "A") + el("b", "Node", "B"),
            extra=f"<views><diagrams>{''.join(views)}</diagrams></views>",
        )
    )


def view(name: str, *boxes: str, identifier: str = "v") -> str:
    return (
        f'<view identifier="{identifier}" xsi:type="Diagram"><name>{name}</name>'
        f"{''.join(boxes)}</view>"
    )


def box(
    ref: str, x: str = "0", y: str = "0", w: str = "100", h: str = "50", ident: str = ""
) -> str:
    return (
        f'<node identifier="n-{ident or ref}-{x}" elementRef="{ref}" xsi:type="Element" '
        f'x="{x}" y="{y}" w="{w}" h="{h}"/>'
    )


async def test_a_view_is_recreated_with_its_boxes_and_never_its_connections(
    importer: ExchangeService, diagrams: InMemoryDiagrams, repository: InMemoryRepository
) -> None:
    report = await importer.import_exchange(xml((DATA / "views-model.xml").read_bytes()), REAL)

    assert report.counts[Category.VIEWS].created == 1
    (diagram,) = diagrams.diagrams.values()
    assert (diagram.name, diagram.description) == ("Overview", "Main picture")
    boxes = diagrams.nodes[diagram.id]
    assert len(boxes) == 2  # the ghost and the repeated element are left out
    names = {repository.elements[b.element_id].name: b for b in boxes}
    assert (names["Alice"].x, names["Alice"].y, names["Alice"].width) == (110.0, 120.0, 120.0)
    assert {"box_omitted", "box_duplicate", "connections_ignored", "shapes_ignored"} <= {
        i.code for i in report.issues
    }
    assert len(repository.relationships) == 1  # the one relationship, not a drawn link


async def test_a_view_never_makes_an_element(
    importer: ExchangeService, diagrams: InMemoryDiagrams, repository: InMemoryRepository
) -> None:
    await importer.import_exchange(a_file(view("V", box("ghost"), box("a"))), REAL)

    assert len(repository.elements) == 2  # a and b, from the file's elements; never "ghost"
    (diagram,) = diagrams.diagrams.values()
    assert [repository.elements[n.element_id].name for n in diagrams.nodes[diagram.id]] == ["A"]


async def test_a_box_may_show_an_element_the_catalogue_already_holds_by_its_source_id(
    importer: ExchangeService,
    service: ArchitectureService,
    diagrams: InMemoryDiagrams,
) -> None:
    existing = await service.create_element(
        element_type=E.NODE, name="old", properties={SOURCE_ID: "known"}
    )

    await importer.import_exchange(
        xml(
            exchange_xml(
                extra=f"<views><diagrams>{view('V', box('known', '5', '6'))}</diagrams></views>"
            )
        ),
        REAL,
    )

    (diagram,) = diagrams.diagrams.values()
    assert [n.element_id for n in diagrams.nodes[diagram.id]] == [existing.id]


async def test_the_same_file_twice_creates_the_view_once(
    importer: ExchangeService, diagrams: InMemoryDiagrams
) -> None:
    content = xml((DATA / "views-model.xml").read_bytes())

    await importer.import_exchange(content, REAL)
    again = await importer.import_exchange(content, REAL)

    assert again.counts[Category.VIEWS].created == 0
    assert again.counts[Category.VIEWS].reused == 1
    assert len(diagrams.diagrams) == 1


async def test_a_name_taken_by_another_layout_gets_an_import_suffix_and_is_then_reused(
    importer: ExchangeService, diagram_service: DiagramService, diagrams: InMemoryDiagrams
) -> None:
    await diagram_service.create(name="Overview")  # same name, no boxes: not the same view
    content = xml((DATA / "views-model.xml").read_bytes())

    await importer.import_exchange(content, REAL)
    again = await importer.import_exchange(content, REAL)

    assert sorted(d.name for d in diagrams.diagrams.values()) == ["Overview", "Overview (import 2)"]
    assert again.counts[Category.VIEWS].reused == 1


async def test_two_views_of_one_name_in_one_file_get_two_names(
    importer: ExchangeService, diagrams: InMemoryDiagrams
) -> None:
    await importer.import_exchange(
        a_file(
            view("V", box("a"), identifier="v1"),
            view("V", box("b"), identifier="v2"),
        ),
        REAL,
    )

    assert sorted(d.name for d in diagrams.diagrams.values()) == ["V", "V (import 2)"]


async def test_a_long_name_is_cut_to_make_room_for_its_suffix(
    importer: ExchangeService, diagram_service: DiagramService, diagrams: InMemoryDiagrams
) -> None:
    name = "N" * 200
    await diagram_service.create(name=name)

    await importer.import_exchange(a_file(view(name, box("a"))), REAL)

    assert sorted(len(d.name) for d in diagrams.diagrams.values()) == [200, 200]
    assert any(d.name.endswith(" (import 2)") for d in diagrams.diagrams.values())


async def test_with_views_false_counts_them_as_ignored(
    importer: ExchangeService, diagrams: InMemoryDiagrams
) -> None:
    report = await importer.import_exchange(
        xml((DATA / "views-model.xml").read_bytes()),
        ImportOptions(dry_run=False, with_views=False),
    )

    assert report.counts[Category.VIEWS].ignored == 1
    assert diagrams.diagrams == {}


async def test_without_a_diagram_service_views_are_ignored_and_it_is_said(
    exchange_store: InMemoryExchange, service: ArchitectureService
) -> None:
    bare = ExchangeService(exchange_store, service, clock=lambda: FIXED_NOW)

    report = await bare.import_exchange(a_file(view("V", box("a"))), REAL)

    assert report.counts[Category.VIEWS].ignored == 1
    assert "views_not_available" in {i.code for i in report.issues}


async def test_a_dry_run_creates_no_view(
    importer: ExchangeService, diagrams: InMemoryDiagrams
) -> None:
    report = await importer.import_exchange(a_file(view("V", box("a"))), ImportOptions())

    assert report.counts[Category.VIEWS].created == 1 and diagrams.diagrams == {}


async def test_a_view_of_more_than_the_ceiling_is_rejected_before_the_database(
    importer: ExchangeService, diagrams: InMemoryDiagrams, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ea.services.exchange_import.MAX_VIEW_BOXES", 1)

    report = await importer.import_exchange(a_file(view("V", box("a"), box("b"))), REAL)

    assert report.counts[Category.VIEWS].rejected == 1
    assert "view_too_large" in {i.code for i in report.issues}
    assert diagrams.diagrams == {}


@pytest.mark.parametrize(
    ("x", "y", "w", "h", "code"),
    [
        ("nan", "0", "100", "50", "invalid_coordinate"),
        ("0", "inf", "100", "50", "invalid_coordinate"),
        ("1e308", "0", "100", "50", "invalid_coordinate"),
        ("-100001", "0", "100", "50", "invalid_coordinate"),
        ("0", "0", "0", "50", "invalid_box_size"),
        ("0", "0", "100", "-5", "invalid_box_size"),
        ("0", "0", "1e308", "50", "invalid_box_size"),
    ],
)
async def test_a_box_with_a_bad_coordinate_is_omitted_with_a_stable_code(
    importer: ExchangeService,
    diagrams: InMemoryDiagrams,
    x: str,
    y: str,
    w: str,
    h: str,
    code: str,
) -> None:
    report = await importer.import_exchange(
        a_file(view("V", box("a", x, y, w, h), box("b", "5", "5"))), REAL
    )

    (diagram,) = diagrams.diagrams.values()
    assert len(diagrams.nodes[diagram.id]) == 1  # only b
    assert code in {i.code for i in report.issues}
    assert report.counts[Category.VIEWS].created == 1


async def test_a_box_for_an_element_the_file_could_not_import_is_omitted(
    importer: ExchangeService, diagrams: InMemoryDiagrams
) -> None:
    content = a_file(
        view("V", box("bad"), box("a")), elements=el("bad", "Node", " ") + el("a", "Node", "A")
    )

    report = await importer.import_exchange(content, REAL)

    (diagram,) = diagrams.diagrams.values()
    assert len(diagrams.nodes[diagram.id]) == 1
    assert "box_omitted" in {i.code for i in report.issues}


async def test_a_view_that_cannot_be_named_is_rejected(
    importer: ExchangeService, diagrams: InMemoryDiagrams
) -> None:
    report = await importer.import_exchange(a_file(view(" ", box("a"))), REAL)

    assert report.counts[Category.VIEWS].rejected == 1
    assert "blank_name" in {i.code for i in report.issues} and diagrams.diagrams == {}


async def test_creating_a_view_is_audited_by_the_diagram_service(
    importer: ExchangeService, repository: InMemoryRepository
) -> None:
    await importer.import_exchange(a_file(view("V", box("a"))), REAL)

    actions = {event.action for event in repository.audit_log.events}
    assert {AuditAction.DIAGRAM_CREATED, AuditAction.DIAGRAM_LAYOUT_REPLACED} <= actions


async def test_a_view_the_database_refuses_is_a_rejected_row_and_the_others_stay(
    importer: ExchangeService,
    diagram_service: DiagramService,
    diagrams: InMemoryDiagrams,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = diagram_service.create

    async def refusing(**kwargs: object):  # type: ignore[no-untyped-def]
        if kwargs["name"] == "bad":
            raise DuplicateDiagramError("a concurrent creation took it")
        return await real(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(diagram_service, "create", refusing)

    report = await importer.import_exchange(
        a_file(
            view("good1", box("a"), identifier="v1"),
            view("bad", box("a"), identifier="v2"),
            view("good2", box("b"), identifier="v3"),
        ),
        REAL,
    )

    assert sorted(d.name for d in diagrams.diagrams.values()) == ["good1", "good2"]
    assert report.counts[Category.VIEWS].rejected == 1
    assert "duplicate_view" in {i.code for i in report.issues}


async def test_the_budget_of_refusals_is_shared_by_the_whole_import(
    importer: ExchangeService, exchange_store: InMemoryExchange
) -> None:
    calls: list[int | None] = []
    real = exchange_store.apply_rows

    async def spy(rows, apply, *, max_refused=None):  # type: ignore[no-untyped-def]
        calls.append(max_refused)
        return await real(rows, apply, max_refused=max_refused)

    exchange_store.apply_rows = spy  # type: ignore[method-assign]

    await importer.import_exchange(a_file(view("V", box("a"))), REAL)

    assert calls == [None, 100]  # nothing refused in the first call: the whole budget remains


async def test_too_many_refusals_across_both_phases_reject_the_file(
    importer: ExchangeService,
    diagram_service: DiagramService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ea.services.exchange_import.MAX_REFUSED_ROWS", 1)

    async def refusing(**kwargs: object):  # type: ignore[no-untyped-def]
        raise DuplicateDiagramError("taken")

    monkeypatch.setattr(diagram_service, "create", refusing)

    with pytest.raises(TooManyRefusedRowsError):
        await importer.import_exchange(
            a_file(
                view("one", box("a"), identifier="v1"),
                view("two", box("a"), identifier="v2"),
            ),
            REAL,
        )


async def test_the_layout_replacing_a_view_is_checked_against_the_catalogue(
    importer: ExchangeService, diagram_service: DiagramService, diagrams: InMemoryDiagrams
) -> None:
    # The service itself refuses a box whose element is gone: the import never relies on it,
    # but it is the net under the import's own resolution.
    diagram = await diagram_service.create(name="X")
    from uuid import uuid4

    from ea.domain.errors import UnknownLayoutElementError

    with pytest.raises(UnknownLayoutElementError):
        await diagram_service.replace_layout(diagram.id, [DiagramNode(uuid4(), 0, 0)])


async def test_a_long_name_that_needed_cutting_is_found_again_on_every_reimport(
    importer: ExchangeService, diagram_service: DiagramService, diagrams: InMemoryDiagrams
) -> None:
    name = "N" * 195  # the suffix ` (import 2)` does not fit: the stored name is cut
    await diagram_service.create(name=name)  # same name, other layout: the copy is suffixed
    content = a_file(view(name, box("a")))

    first = await importer.import_exchange(content, REAL)
    for _ in range(3):
        again = await importer.import_exchange(content, REAL)
        assert again.counts[Category.VIEWS].created == 0
        assert again.counts[Category.VIEWS].reused == 1

    assert first.counts[Category.VIEWS].created == 1
    assert len(diagrams.diagrams) == 2  # the original, and one copy — however often it is sent


async def test_a_view_whose_every_box_was_omitted_is_not_created_empty(
    importer: ExchangeService, diagrams: InMemoryDiagrams
) -> None:
    report = await importer.import_exchange(a_file(view("V", box("ghost"), box("nowhere"))), REAL)

    assert report.counts[Category.VIEWS].rejected == 1
    assert diagrams.diagrams == {}
    codes = {i.code for i in report.issues}
    assert {"view_without_boxes", "box_omitted"} <= codes


async def test_a_view_that_never_had_a_box_is_still_a_view(
    importer: ExchangeService, diagrams: InMemoryDiagrams
) -> None:
    report = await importer.import_exchange(a_file(view("Empty on purpose")), REAL)

    assert report.counts[Category.VIEWS].created == 1
    assert [d.name for d in diagrams.diagrams.values()] == ["Empty on purpose"]
