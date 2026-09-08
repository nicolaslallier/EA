"""Cutting a document up, embedding the pieces, and finding them again.

The indexer is the seam between two pure things — the chunker and an embedding
service — and everything it decides is about *what* gets embedded: the trail of
headings in front of each passage, the whole document in one call, and the name
of the model recorded beside every vector. See docs/adr/0019.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType
from ea.domain.documents import Document
from ea.domain.errors import SearchUnavailableError
from ea.domain.model import Element
from ea.domain.search import EMBEDDING_DIMENSIONS
from ea.services.architecture import ArchitectureService
from ea.services.documents import DocumentService
from ea.services.indexing import DocumentIndexer
from tests.conftest import FIXED_NOW, FakeEmbedder

pytestmark = pytest.mark.asyncio

RUNBOOK = "# Runbook\n\nQuoi faire.\n\n## Escalade\n\nAppeler Nicolas.\n"


def a_document(content: str = RUNBOOK, filename: str = "runbook.md") -> Document:
    return Document.create(element_id=uuid4(), filename=filename, content=content, now=FIXED_NOW)


async def an_element(service: ArchitectureService, name: str = "Facturation") -> Element:
    return await service.create_element(element_type=ElementType.APPLICATION_COMPONENT, name=name)


class TestIndexingADocument:
    async def test_one_passage_per_section(self) -> None:
        passages = await DocumentIndexer(FakeEmbedder()).passages_of(a_document())

        assert [passage.heading_path for passage in passages] == [
            ("Runbook",),
            ("Runbook", "Escalade"),
        ]

    async def test_what_is_embedded_is_the_trail_and_then_the_passage(self) -> None:
        """The point of docs/adr/0019: "Appeler Nicolas" alone means nothing."""
        embedder = FakeEmbedder()

        await DocumentIndexer(embedder).passages_of(a_document())

        assert embedder.passages == [
            "runbook.md > Runbook\n\nQuoi faire.",
            "runbook.md > Runbook > Escalade\n\nAppeler Nicolas.",
        ]

    async def test_the_stored_text_is_the_passage_without_the_trail(self) -> None:
        """The trail is context for the model; showing it back twice is noise."""
        passages = await DocumentIndexer(FakeEmbedder()).passages_of(a_document())

        assert passages[1].text == "Appeler Nicolas."

    async def test_every_passage_records_the_model_that_embedded_it(self) -> None:
        passages = await DocumentIndexer(FakeEmbedder(model="bge-m3")).passages_of(a_document())

        assert {passage.model for passage in passages} == {"bge-m3"}

    async def test_each_passage_keeps_a_vector_of_its_own(self) -> None:
        passages = await DocumentIndexer(FakeEmbedder()).passages_of(a_document())

        assert len({passage.embedding for passage in passages}) == len(passages)

    async def test_the_whole_document_is_embedded_in_one_call(self) -> None:
        """Batching is the client's business; the indexer must not defeat it."""
        embedder = FakeEmbedder()

        await DocumentIndexer(embedder).passages_of(a_document())

        assert embedder.calls == 1

    async def test_a_document_that_cuts_into_nothing_asks_the_service_nothing(self) -> None:
        """A file holding front matter and no prose — legal, and not a passage."""
        embedder = FakeEmbedder()

        passages = await DocumentIndexer(embedder).passages_of(
            a_document(content="---\ntitle: vide\n---\n")
        )

        assert passages == ()
        assert embedder.calls == 0

    async def test_the_vectors_are_the_width_the_column_stores(self) -> None:
        """Otherwise these tests would pass on vectors the real store refuses."""
        passages = await DocumentIndexer(FakeEmbedder()).passages_of(a_document())

        assert {len(passage.embedding) for passage in passages} == {EMBEDDING_DIMENSIONS}


class TestQuerying:
    async def test_a_query_is_embedded_as_a_query_and_not_as_a_passage(self) -> None:
        """Swapping the two costs nothing visible and a good deal of recall."""
        embedder = FakeEmbedder()

        await DocumentIndexer(embedder).embed_query("où redémarrer ?")

        assert embedder.queries == ["où redémarrer ?"]
        assert embedder.passages == []

    async def test_the_indexer_reports_the_model_it_indexes_with(self) -> None:
        """It is the filter a search runs with, so it has to be the same string."""
        assert DocumentIndexer(FakeEmbedder(model="e5")).model == "e5"


class TestWhenThereIsNoIndex:
    async def test_searching_without_one_says_so_rather_than_crashing(
        self, document_service_without_an_index: DocumentService
    ) -> None:
        """`EA_EMBEDDINGS_ENABLED` off is a configuration, so it gets a sentence."""
        with pytest.raises(SearchUnavailableError, match="not enabled"):
            await document_service_without_an_index.search("quoi que ce soit")

    async def test_documents_are_still_stored_and_simply_not_indexed(
        self, document_service_without_an_index: DocumentService, service: ArchitectureService
    ) -> None:
        """An upload must not depend on a second service being up."""
        element = await an_element(service)

        stored = await document_service_without_an_index.attach_text(
            element.id, filename="runbook.md", content=RUNBOOK
        )

        assert stored.filename == "runbook.md"

    async def test_reindexing_without_one_says_so_too(
        self, document_service_without_an_index: DocumentService
    ) -> None:
        with pytest.raises(SearchUnavailableError):
            await document_service_without_an_index.reindex_all()


class TestReindexing:
    async def test_it_rebuilds_the_passages_of_every_stored_document(
        self,
        document_service: DocumentService,
        service: ArchitectureService,
        embedder: FakeEmbedder,
    ) -> None:
        """The catch-up for documents attached while the index was off."""
        element = await an_element(service)
        await document_service.attach_text(element.id, filename="a.md", content=RUNBOOK)
        await document_service.attach_text(element.id, filename="b.md", content=RUNBOOK)
        embedder.reset()

        indexed = await document_service.reindex_all()

        assert indexed == 2
        assert embedder.calls == 2

    async def test_it_leaves_the_documents_themselves_untouched(
        self, document_service: DocumentService, service: ArchitectureService
    ) -> None:
        """Rebuilding an index is not a revision: nothing about the file changed."""
        element = await an_element(service)
        stored = await document_service.attach_text(element.id, filename="a.md", content=RUNBOOK)

        await document_service.reindex_all()

        after = await document_service.get(stored.id)
        assert (after.content, after.updated_at) == (stored.content, stored.updated_at)


class TestSearching:
    async def test_it_finds_the_passage_whose_trail_matches_the_question(
        self, document_service: DocumentService, service: ArchitectureService
    ) -> None:
        """`FakeEmbedder` scores on shared words — enough to prove the wiring."""
        element = await an_element(service)
        await document_service.attach_text(element.id, filename="runbook.md", content=RUNBOOK)

        hits = await document_service.search("Escalade")

        assert hits[0].heading_path == ("Runbook", "Escalade")
        assert hits[0].trail == "runbook.md > Runbook > Escalade"

    async def test_a_search_can_be_scoped_to_one_element(
        self, document_service: DocumentService, service: ArchitectureService
    ) -> None:
        first = await an_element(service, "Facturation")
        second = await an_element(service, "Commandes")
        await document_service.attach_text(first.id, filename="a.md", content=RUNBOOK)
        await document_service.attach_text(second.id, filename="b.md", content=RUNBOOK)

        hits = await document_service.search("Escalade", element_id=second.id)

        assert {hit.element_id for hit in hits} == {second.id}

    async def test_scoping_to_an_element_that_does_not_exist_is_refused(
        self, document_service: DocumentService
    ) -> None:
        """An empty result would read as "this element has nothing written about it"."""
        from ea.domain.errors import ElementNotFoundError

        with pytest.raises(ElementNotFoundError):
            await document_service.search("Escalade", element_id=uuid4())

    async def test_a_revision_is_never_searchable_under_its_previous_text(
        self, document_service: DocumentService, service: ArchitectureService
    ) -> None:
        element = await an_element(service)
        stored = await document_service.attach_text(
            element.id, filename="runbook.md", content=RUNBOOK
        )

        await document_service.revise_text(
            stored.id, filename="runbook.md", content="# Runbook\n\n## Bascule\n\nBasculer.\n"
        )
        hits = await document_service.search("Escalade")

        assert all(hit.heading_path != ("Runbook", "Escalade") for hit in hits)

    async def test_a_discarded_document_takes_its_passages_with_it(
        self, document_service: DocumentService, service: ArchitectureService
    ) -> None:
        element = await an_element(service)
        stored = await document_service.attach_text(
            element.id, filename="runbook.md", content=RUNBOOK
        )

        await document_service.discard(stored.id)

        assert await document_service.search("Escalade") == ()

    async def test_deleting_the_element_empties_the_index_of_its_documents(
        self, document_service: DocumentService, service: ArchitectureService
    ) -> None:
        element = await an_element(service)
        await document_service.attach_text(element.id, filename="runbook.md", content=RUNBOOK)

        await service.delete_element(element.id)

        assert await document_service.search("Escalade") == ()

    async def test_passages_embedded_by_another_model_are_not_matches(
        self,
        document_service: DocumentService,
        service: ArchitectureService,
        embedder: FakeEmbedder,
    ) -> None:
        """A corpus half re-embedded returns too little, visibly — never nonsense."""
        element = await an_element(service)
        await document_service.attach_text(element.id, filename="runbook.md", content=RUNBOOK)

        embedder.rename("un-autre-modele")

        assert await document_service.search("Escalade") == ()

    async def test_asking_for_more_hits_than_the_bound_is_refused(
        self, document_service: DocumentService
    ) -> None:
        with pytest.raises(ValueError, match="at most"):
            await document_service.search("Escalade", limit=10_000)

    async def test_a_blank_question_is_refused_rather_than_embedded(
        self, document_service: DocumentService
    ) -> None:
        with pytest.raises(ValueError, match="question"):
            await document_service.search("   ")
