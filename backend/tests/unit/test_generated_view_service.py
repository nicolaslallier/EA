from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType, RelationshipType
from ea.domain.diagram_objects import DiagramEdge, DiagramObject, ObjectKind, Point
from ea.domain.diagrams import DiagramNode
from ea.domain.errors import (
    DiagramHasAnnotationsError,
    DiagramLiveError,
    DiagramNotFoundError,
    DiagramNotGeneratedError,
    DiagramNotLiveError,
    GeneratedViewsUnavailableError,
    GeneratedViewTooLargeError,
    NotAuthorisedError,
)
from ea.domain.generated_views import Generation, PackageSource, QuerySource
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.diagrams import DiagramService
from tests.conftest import InMemoryDiagrams, InMemoryViewSources, a_reader, lay_out


async def element(service: ArchitectureService, name: str):
    return await service.create_element(element_type=ElementType.APPLICATION_COMPONENT, name=name)


@pytest.mark.asyncio
class TestLive:
    async def test_a_live_view_stores_no_box_and_shows_what_the_source_yields_now(
        self,
        diagram_service: DiagramService,
        service: ArchitectureService,
        view_sources: InMemoryViewSources,
    ) -> None:
        a, b = await element(service, "A"), await element(service, "B")
        link = await service.connect(
            relationship_type=RelationshipType.ASSOCIATION, source_id=a.id, target_id=b.id
        )
        source = QuerySource(uuid4())
        view_sources.answers[source] = [a.id, b.id]

        opened = await diagram_service.generate(name="Périmètre", source=source)

        assert opened.diagram.generation is Generation.LIVE
        assert opened.nodes == () and opened.objects == ()
        assert {e.id for e in opened.graph.elements} == {a.id, b.id}
        assert [r.id for r in opened.graph.relationships] == [link.id]
        view_sources.answers[source] = [a.id]
        reopened = await diagram_service.open(opened.diagram.id)
        assert {e.id for e in reopened.graph.elements} == {a.id}

    async def test_a_source_that_does_not_fit_is_refused_with_the_count(
        self, diagram_service: DiagramService, view_sources: InMemoryViewSources
    ) -> None:
        source = PackageSource(uuid4(), recursive=True)
        view_sources.answers[source] = [uuid4() for _ in range(501)]

        with pytest.raises(GeneratedViewTooLargeError, match="501"):
            await diagram_service.generate(name="Trop", source=source)

        assert await diagram_service.list_diagrams() == ()

    async def test_a_live_view_whose_source_outgrew_the_limit_says_how_out_and_can_be_deleted(
        self,
        diagram_service: DiagramService,
        service: ArchitectureService,
        view_sources: InMemoryViewSources,
    ) -> None:
        a = await element(service, "A")
        source = QuerySource(uuid4())
        view_sources.answers[source] = [a.id]
        opened = await diagram_service.generate(name="Grandit", source=source)
        view_sources.answers[source] = [uuid4() for _ in range(501)]

        with pytest.raises(GeneratedViewTooLargeError, match=r"501.*delete this view"):
            await diagram_service.open(opened.diagram.id)

        await diagram_service.delete(opened.diagram.id)  # reading it is not needed to remove it
        assert await diagram_service.list_diagrams() == ()

    async def test_as_of_marks_what_is_out_of_period_on_a_live_view_too(
        self,
        diagram_service: DiagramService,
        service: ArchitectureService,
        view_sources: InMemoryViewSources,
    ) -> None:
        old = await service.create_element(
            element_type=ElementType.APPLICATION_COMPONENT,
            name="Old",
            properties={"valid_until": "2020-12-31"},
        )
        source = QuerySource(uuid4())
        view_sources.answers[source] = [old.id]
        opened = await diagram_service.generate(name="Vivante", source=source)

        later = await diagram_service.open(opened.diagram.id, as_of=date(2026, 1, 1))

        assert old.id in later.inactive_elements

    async def test_a_live_view_cannot_be_laid_out(
        self, diagram_service: DiagramService, view_sources: InMemoryViewSources
    ) -> None:
        source = QuerySource(uuid4())
        view_sources.answers[source] = []
        opened = await diagram_service.generate(name="Vue", source=source)

        with pytest.raises(DiagramLiveError):
            await lay_out(diagram_service, opened.diagram.id, [])

    async def test_a_reader_cannot_generate(self, diagram_service: DiagramService) -> None:
        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await diagram_service.generate(name="Vue", source=QuerySource(uuid4()))

    async def test_without_sub_project_one_a_generation_says_it_is_unavailable(
        self, diagrams, service: ArchitectureService
    ) -> None:
        bare = DiagramService(diagrams, service)

        with pytest.raises(GeneratedViewsUnavailableError):
            await bare.generate(name="Vue", source=QuerySource(uuid4()))


@pytest.mark.asyncio
class TestFreezing:
    async def _live(self, diagram_service, service, view_sources):
        a, b = await element(service, "A"), await element(service, "B")
        source = QuerySource(uuid4())
        view_sources.answers[source] = [a.id, b.id]
        opened = await diagram_service.generate(name="Vue", source=source)
        return opened.diagram, a, b, source

    async def test_freezing_stores_the_layout_the_spa_computed_and_keeps_the_source(
        self,
        diagram_service: DiagramService,
        service: ArchitectureService,
        view_sources: InMemoryViewSources,
    ) -> None:
        diagram, a, b, source = await self._live(diagram_service, service, view_sources)

        frozen = await diagram_service.freeze(
            diagram.id, revision=0, nodes=[DiagramNode(a.id, 10, 10), DiagramNode(b.id, 200, 10)]
        )
        opened = await diagram_service.open(diagram.id)

        assert frozen.generation is Generation.FROZEN and frozen.source == source
        assert {n.element_id for n in opened.nodes} == {a.id, b.id}

    async def test_a_box_that_is_not_in_the_source_is_refused(
        self,
        diagram_service: DiagramService,
        service: ArchitectureService,
        view_sources: InMemoryViewSources,
    ) -> None:
        diagram, _, _, _ = await self._live(diagram_service, service, view_sources)
        stranger = await element(service, "Étranger")

        with pytest.raises(ValueError, match="source"):
            await diagram_service.freeze(
                diagram.id, revision=0, nodes=[DiagramNode(stranger.id, 0, 0)]
            )

    async def test_a_frozen_view_is_edited_like_a_hand_drawn_one(
        self,
        diagram_service: DiagramService,
        service: ArchitectureService,
        view_sources: InMemoryViewSources,
    ) -> None:
        diagram, a, b, _ = await self._live(diagram_service, service, view_sources)
        await diagram_service.freeze(
            diagram.id, revision=0, nodes=[DiagramNode(a.id, 0, 0), DiagramNode(b.id, 200, 0)]
        )

        revision = await lay_out(diagram_service, diagram.id, [DiagramNode(a.id, 5, 5)])

        assert revision == 2  # the freeze was revision 1

    async def test_freezing_twice_or_freezing_a_hand_drawn_diagram_is_refused(
        self,
        diagram_service: DiagramService,
        service: ArchitectureService,
        view_sources: InMemoryViewSources,
    ) -> None:
        diagram, a, _, _ = await self._live(diagram_service, service, view_sources)
        await diagram_service.freeze(diagram.id, revision=0, nodes=[DiagramNode(a.id, 0, 0)])
        by_hand = await diagram_service.create(name="À la main")

        with pytest.raises(DiagramNotLiveError):
            await diagram_service.freeze(diagram.id, revision=0, nodes=[])
        with pytest.raises(DiagramNotGeneratedError):
            await diagram_service.freeze(by_hand.id, revision=0, nodes=[])

    async def test_a_view_deleted_while_it_is_frozen_is_not_found(
        self,
        diagram_service: DiagramService,
        service: ArchitectureService,
        view_sources: InMemoryViewSources,
        diagrams: InMemoryDiagrams,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        diagram, a, _, _ = await self._live(diagram_service, service, view_sources)

        async def gone(*_: object, **__: object) -> None:
            return None

        monkeypatch.setattr(diagrams, "freeze", gone)

        with pytest.raises(DiagramNotFoundError):
            await diagram_service.freeze(diagram.id, revision=0, nodes=[DiagramNode(a.id, 0, 0)])

    async def test_refreshing_shows_the_gap_and_writes_nothing(
        self,
        diagram_service: DiagramService,
        service: ArchitectureService,
        view_sources: InMemoryViewSources,
    ) -> None:
        diagram, a, b, source = await self._live(diagram_service, service, view_sources)
        await diagram_service.freeze(
            diagram.id, revision=0, nodes=[DiagramNode(a.id, 0, 0), DiagramNode(b.id, 200, 0)]
        )
        c = await element(service, "C")
        view_sources.answers[source] = [a.id, c.id]

        diff = await diagram_service.source_diff(diagram.id)

        assert [e.id for e in diff.added] == [c.id]
        assert diff.removed == (b.id,)
        assert {n.element_id for n in (await diagram_service.open(diagram.id)).nodes} == {
            a.id,
            b.id,
        }

    async def test_a_source_that_vanished_is_a_not_found_not_a_crash(
        self,
        diagram_service: DiagramService,
        service: ArchitectureService,
        view_sources: InMemoryViewSources,
    ) -> None:
        diagram, a, _, source = await self._live(diagram_service, service, view_sources)
        await diagram_service.freeze(diagram.id, revision=0, nodes=[DiagramNode(a.id, 0, 0)])
        del view_sources.answers[source]

        with pytest.raises(Exception, match="no such source"):
            await diagram_service.source_diff(diagram.id)


@pytest.mark.asyncio
class TestThawing:
    async def _frozen(self, diagram_service, service, view_sources):
        a = await element(service, "A")
        source = QuerySource(uuid4())
        view_sources.answers[source] = [a.id]
        opened = await diagram_service.generate(name="Vue", source=source)
        await diagram_service.freeze(opened.diagram.id, revision=0, nodes=[DiagramNode(a.id, 0, 0)])
        return opened.diagram, a

    async def test_thawing_a_view_without_annotations_goes_back_to_live(
        self,
        diagram_service: DiagramService,
        service: ArchitectureService,
        view_sources: InMemoryViewSources,
    ) -> None:
        diagram, _ = await self._frozen(diagram_service, service, view_sources)

        thawed = await diagram_service.unfreeze(
            diagram.id, revision=(await diagram_service.get(diagram.id)).revision
        )
        opened = await diagram_service.open(diagram.id)

        assert thawed.generation is Generation.LIVE
        assert opened.nodes == ()

    async def test_a_view_deleted_while_it_is_thawed_is_not_found(
        self,
        diagram_service: DiagramService,
        service: ArchitectureService,
        view_sources: InMemoryViewSources,
        diagrams: InMemoryDiagrams,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        diagram, _ = await self._frozen(diagram_service, service, view_sources)

        async def gone(*_: object, **__: object) -> None:
            return None

        monkeypatch.setattr(diagrams, "unfreeze", gone)

        with pytest.raises(DiagramNotFoundError):
            await diagram_service.unfreeze(
                diagram.id, revision=(await diagram_service.get(diagram.id)).revision
            )

    async def test_thawing_refuses_to_lose_a_note_unless_told_to(
        self,
        diagram_service: DiagramService,
        service: ArchitectureService,
        view_sources: InMemoryViewSources,
    ) -> None:
        diagram, a = await self._frozen(diagram_service, service, view_sources)
        note = DiagramObject(uuid4(), ObjectKind.NOTE, 0, 0, 100, 50, text="à voir")
        await lay_out(diagram_service, diagram.id, [DiagramNode(a.id, 0, 0)], [note])

        with pytest.raises(DiagramHasAnnotationsError):
            await diagram_service.unfreeze(
                diagram.id, revision=(await diagram_service.get(diagram.id)).revision
            )
        thawed = await diagram_service.unfreeze(
            diagram.id,
            revision=(await diagram_service.get(diagram.id)).revision,
            discard_annotations=True,
        )

        assert thawed.generation is Generation.LIVE
        assert (await diagram_service.open(diagram.id)).objects == ()

    async def test_a_custom_route_counts_as_an_annotation(
        self,
        diagram_service: DiagramService,
        service: ArchitectureService,
        view_sources: InMemoryViewSources,
    ) -> None:
        a, b = await element(service, "A"), await element(service, "B")
        link = await service.connect(
            relationship_type=RelationshipType.ASSOCIATION, source_id=a.id, target_id=b.id
        )
        source = QuerySource(uuid4())
        view_sources.answers[source] = [a.id, b.id]
        opened = await diagram_service.generate(name="Vue", source=source)
        await diagram_service.freeze(
            opened.diagram.id,
            revision=0,
            nodes=[DiagramNode(a.id, 0, 0), DiagramNode(b.id, 300, 0)],
        )
        await lay_out(
            diagram_service,
            opened.diagram.id,
            [DiagramNode(a.id, 0, 0), DiagramNode(b.id, 300, 0)],
            edges=[DiagramEdge(link.id, (Point(150, 100),))],
        )

        with pytest.raises(DiagramHasAnnotationsError):
            await diagram_service.unfreeze(
                opened.diagram.id, revision=(await diagram_service.get(opened.diagram.id)).revision
            )

    async def test_a_live_view_cannot_be_thawed(
        self, diagram_service: DiagramService, view_sources: InMemoryViewSources
    ) -> None:
        source = QuerySource(uuid4())
        view_sources.answers[source] = []
        opened = await diagram_service.generate(name="Vue", source=source)

        with pytest.raises(DiagramLiveError):
            await diagram_service.unfreeze(
                opened.diagram.id, revision=(await diagram_service.get(opened.diagram.id)).revision
            )
