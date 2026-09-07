"""The use cases over attached markdown, against an in-memory pair of stores.

The rule this layer owns is the one no foreign key can state: the element is a
node in Neo4j and the document a row in PostgreSQL, so "attach to an element
that exists" and "delete the documents when the element goes" are both code —
see docs/adr/0017. These are the tests of that code.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType
from ea.domain.errors import (
    DocumentNotFoundError,
    DuplicateDocumentError,
    ElementNotFoundError,
)
from ea.domain.model import Element
from ea.services.architecture import ArchitectureService
from ea.services.documents import DocumentService
from tests.conftest import FIXED_NOW, InMemoryDocuments

pytestmark = pytest.mark.asyncio

RUNBOOK = b"# Runbook\n\nRedemarrer le service.\n"


async def an_element(service: ArchitectureService, name: str = "Facturation") -> Element:
    return await service.create_element(element_type=ElementType.APPLICATION_COMPONENT, name=name)


class TestAttaching:
    async def test_a_file_is_attached_to_an_element_that_exists(
        self, service: ArchitectureService, document_service: DocumentService
    ) -> None:
        element = await an_element(service)

        document = await document_service.attach(element.id, filename="runbook.md", raw=RUNBOOK)

        assert document.element_id == element.id
        assert document.content.startswith("# Runbook")
        assert document.created_at == FIXED_NOW

    async def test_attaching_to_an_element_that_does_not_exist_is_refused(
        self, document_service: DocumentService
    ) -> None:
        """No foreign key says this, so the service has to.

        Without the check the row would be written and then unreachable: the
        listing goes through the element, which does not exist.
        """
        with pytest.raises(ElementNotFoundError):
            await document_service.attach(uuid4(), filename="runbook.md", raw=RUNBOOK)

    async def test_the_same_file_name_twice_on_one_element_is_refused(
        self, service: ArchitectureService, document_service: DocumentService
    ) -> None:
        element = await an_element(service)
        await document_service.attach(element.id, filename="runbook.md", raw=RUNBOOK)

        with pytest.raises(DuplicateDocumentError):
            await document_service.attach(element.id, filename="runbook.md", raw=RUNBOOK)

    async def test_the_same_file_name_on_two_elements_is_fine(
        self, service: ArchitectureService, document_service: DocumentService
    ) -> None:
        """`README.md` is the name half the repositories in the world use."""
        first = await an_element(service, "Facturation")
        second = await an_element(service, "Commandes")

        await document_service.attach(first.id, filename="README.md", raw=RUNBOOK)
        await document_service.attach(second.id, filename="README.md", raw=RUNBOOK)

        assert len(await document_service.list_for_element(second.id)) == 1

    async def test_a_file_the_domain_refuses_never_reaches_the_store(
        self, service: ArchitectureService, document_service: DocumentService
    ) -> None:
        element = await an_element(service)

        with pytest.raises(ValueError, match="UTF-8"):
            await document_service.attach(element.id, filename="notes.md", raw=b"\xff\xfe\x00x")

        assert await document_service.list_for_element(element.id) == ()


class TestAttachingText:
    """The entry point an adapter with no upload uses — the MCP tools.

    An agent composes a document, it never sends a file, so the text skips
    `decode_markdown` and lands on `Document.create` directly. What these check
    is that skipping the decoding skips *nothing else*.
    """

    async def test_text_is_attached_the_same_way_an_upload_is(
        self, service: ArchitectureService, document_service: DocumentService
    ) -> None:
        element = await an_element(service)

        document = await document_service.attach_text(
            element.id, filename="runbook.md", content="# Runbook\n"
        )

        assert document.content == "# Runbook\n"
        assert document.byte_size == len(b"# Runbook\n")

    async def test_the_element_must_exist_for_text_too(
        self, document_service: DocumentService
    ) -> None:
        with pytest.raises(ElementNotFoundError):
            await document_service.attach_text(
                uuid4(), filename="runbook.md", content="# Runbook\n"
            )

    async def test_a_name_that_is_not_markdown_is_refused_for_text_too(
        self, service: ArchitectureService, document_service: DocumentService
    ) -> None:
        element = await an_element(service)

        with pytest.raises(ValueError, match=r"\.md"):
            await document_service.attach_text(element.id, filename="notes.txt", content="plain\n")

    async def test_empty_text_is_a_failed_document_rather_than_a_stored_one(
        self, service: ArchitectureService, document_service: DocumentService
    ) -> None:
        element = await an_element(service)

        with pytest.raises(ValueError, match="empty"):
            await document_service.attach_text(element.id, filename="runbook.md", content="   \n")

        assert await document_service.list_for_element(element.id) == ()

    async def test_text_holding_a_nul_is_refused_though_nothing_was_decoded(
        self, service: ArchitectureService, document_service: DocumentService
    ) -> None:
        """PostgreSQL cannot hold a NUL in a `TEXT` column whoever composed it."""
        element = await an_element(service)

        with pytest.raises(ValueError, match="NUL"):
            await document_service.attach_text(element.id, filename="runbook.md", content="a\x00b")


class TestReading:
    async def test_a_listing_names_the_files_without_carrying_their_text(
        self, service: ArchitectureService, document_service: DocumentService
    ) -> None:
        element = await an_element(service)
        await document_service.attach(element.id, filename="runbook.md", raw=RUNBOOK)

        listed = await document_service.list_for_element(element.id)

        assert [summary.filename for summary in listed] == ["runbook.md"]
        assert listed[0].byte_size == len(RUNBOOK)

    async def test_listing_an_element_that_does_not_exist_is_not_an_empty_list(
        self, document_service: DocumentService
    ) -> None:
        """ "No such element" and "no documents" are different answers."""
        with pytest.raises(ElementNotFoundError):
            await document_service.list_for_element(uuid4())

    async def test_reading_a_document_gives_back_the_markdown(
        self, service: ArchitectureService, document_service: DocumentService
    ) -> None:
        element = await an_element(service)
        stored = await document_service.attach(element.id, filename="runbook.md", raw=RUNBOOK)

        assert (await document_service.get(stored.id)).content == RUNBOOK.decode()

    async def test_reading_a_document_that_is_gone_says_so(
        self, document_service: DocumentService
    ) -> None:
        with pytest.raises(DocumentNotFoundError):
            await document_service.get(uuid4())


class TestRevising:
    async def test_a_new_version_of_the_same_file_replaces_the_content(
        self, service: ArchitectureService, document_service: DocumentService
    ) -> None:
        element = await an_element(service)
        stored = await document_service.attach(element.id, filename="runbook.md", raw=RUNBOOK)

        revised = await document_service.revise(
            stored.id, filename="runbook.md", raw=b"# Runbook v2\n"
        )

        assert revised.id == stored.id
        assert (await document_service.get(stored.id)).content == "# Runbook v2\n"

    async def test_uploading_a_different_file_over_a_document_is_refused(
        self, service: ArchitectureService, document_service: DocumentService
    ) -> None:
        """A reader knows a document by its name; the name must keep its text."""
        element = await an_element(service)
        stored = await document_service.attach(element.id, filename="runbook.md", raw=RUNBOOK)

        with pytest.raises(ValueError, match=r"notes\.md"):
            await document_service.revise(stored.id, filename="notes.md", raw=b"# Autre chose\n")


class TestRevisingText:
    async def test_text_replaces_the_content_under_the_same_id(
        self, service: ArchitectureService, document_service: DocumentService
    ) -> None:
        element = await an_element(service)
        stored = await document_service.attach(element.id, filename="runbook.md", raw=RUNBOOK)

        revised = await document_service.revise_text(
            stored.id, filename="runbook.md", content="# Runbook v2\n"
        )

        assert revised.id == stored.id
        assert (await document_service.get(stored.id)).content == "# Runbook v2\n"

    async def test_the_name_still_has_to_match_when_the_content_is_text(
        self, service: ArchitectureService, document_service: DocumentService
    ) -> None:
        element = await an_element(service)
        stored = await document_service.attach(element.id, filename="runbook.md", raw=RUNBOOK)

        with pytest.raises(ValueError, match=r"runbook\.md"):
            await document_service.revise_text(
                stored.id, filename="notes.md", content="# Something else\n"
            )


class TestDiscarding:
    async def test_a_document_can_be_removed_on_its_own(
        self, service: ArchitectureService, document_service: DocumentService
    ) -> None:
        element = await an_element(service)
        stored = await document_service.attach(element.id, filename="runbook.md", raw=RUNBOOK)

        await document_service.discard(stored.id)

        assert await document_service.list_for_element(element.id) == ()

    async def test_removing_a_document_twice_says_it_is_gone(
        self, service: ArchitectureService, document_service: DocumentService
    ) -> None:
        element = await an_element(service)
        stored = await document_service.attach(element.id, filename="runbook.md", raw=RUNBOOK)
        await document_service.discard(stored.id)

        with pytest.raises(DocumentNotFoundError):
            await document_service.discard(stored.id)

    async def test_deleting_the_element_takes_its_documents_with_it(
        self,
        service: ArchitectureService,
        document_service: DocumentService,
        documents: InMemoryDocuments,
    ) -> None:
        """The cascade PostgreSQL cannot declare, because the element is a node.

        Checked on the store rather than through the API, since after the
        deletion there is no element left to list the documents of — which is
        exactly how orphaned rows would stay invisible.
        """
        element = await an_element(service)
        await document_service.attach(element.id, filename="runbook.md", raw=RUNBOOK)

        await service.delete_element(element.id)

        assert documents.documents == {}

    async def test_deleting_one_element_leaves_another_element_its_documents(
        self, service: ArchitectureService, document_service: DocumentService
    ) -> None:
        first = await an_element(service, "Facturation")
        second = await an_element(service, "Commandes")
        await document_service.attach(first.id, filename="a.md", raw=RUNBOOK)
        await document_service.attach(second.id, filename="b.md", raw=RUNBOOK)

        await service.delete_element(first.id)

        assert len(await document_service.list_for_element(second.id)) == 1

    async def test_an_architecture_service_without_a_store_still_deletes_elements(
        self, repository: object
    ) -> None:
        """The relational store shut is the one case with nothing to discard."""
        alone = ArchitectureService(repository, clock=lambda: FIXED_NOW)  # type: ignore[arg-type]
        element = await an_element(alone)

        await alone.delete_element(element.id)

        with pytest.raises(ElementNotFoundError):
            await alone.get_element(element.id)
