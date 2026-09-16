"""The file use cases, over the in-memory store: the rules, not MinIO."""

from __future__ import annotations

import pytest

from ea.domain.errors import (
    FileNotTextError,
    FileStorageUnavailableError,
    FileTooLargeError,
    InvalidFileKeyError,
    InvalidFileMetadataError,
    NotAuthorisedError,
    StoredFileExistsError,
    StoredFileNotFoundError,
)
from ea.domain.files import (
    MAX_FILE_BYTES,
    MAX_TEXT_READ_BYTES,
    FileDetails,
    ReconcileReport,
    sha256_of,
)
from ea.services.caller import acting_as
from ea.services.files import FileService
from tests.conftest import (
    FIXED_NOW,
    InMemoryFileMetadata,
    InMemoryObjectStore,
    a_reader,
    an_editor,
)

pytestmark = pytest.mark.asyncio


class TestUploading:
    async def test_a_file_is_stored_under_its_key(
        self, file_service: FileService, files_store: InMemoryObjectStore
    ) -> None:
        stored = await file_service.upload("inbox/a.md", b"# A", content_type="text/markdown")

        assert (stored.key, stored.size) == ("inbox/a.md", 3)
        assert files_store.objects["inbox/a.md"] == (b"# A", "text/markdown")

    async def test_a_second_upload_on_the_same_key_is_refused(
        self, file_service: FileService
    ) -> None:
        await file_service.upload("a.md", b"one")
        with pytest.raises(StoredFileExistsError):
            await file_service.upload("a.md", b"two")

    async def test_overwrite_replaces_it(
        self, file_service: FileService, files_store: InMemoryObjectStore
    ) -> None:
        await file_service.upload("a.md", b"one")
        await file_service.upload("a.md", b"two", overwrite=True)
        assert files_store.objects["a.md"][0] == b"two"

    async def test_a_file_over_the_limit_is_refused(self, file_service: FileService) -> None:
        with pytest.raises(FileTooLargeError):
            await file_service.upload("big.bin", b"\0" * (MAX_FILE_BYTES + 1))

    async def test_an_ambiguous_path_is_refused(self, file_service: FileService) -> None:
        with pytest.raises(InvalidFileKeyError):
            await file_service.upload("inbox/../a.md", b"x")

    async def test_a_reader_cannot_upload(self, file_service: FileService) -> None:
        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await file_service.upload("a.md", b"x")


class TestReading:
    async def test_a_reader_lists_a_folder(self, file_service: FileService) -> None:
        await file_service.upload("inbox/a.md", b"x")
        with acting_as(a_reader()):
            listing = await file_service.list_folder("inbox")
        assert [f.key for f in listing.files] == ["inbox/a.md"]
        assert listing.prefix == "inbox/"

    async def test_text_is_read_as_text(self, file_service: FileService) -> None:
        await file_service.upload("a.md", "é".encode())
        _, text = await file_service.read_text("a.md")
        assert text == "é"

    async def test_bytes_that_are_not_utf8_are_not_text(self, file_service: FileService) -> None:
        await file_service.upload("a.pdf", b"%PDF\xff\xfe")
        with pytest.raises(FileNotTextError):
            await file_service.read_text("a.pdf")

    async def test_a_text_too_long_to_be_an_answer_is_refused(
        self, file_service: FileService
    ) -> None:
        await file_service.upload("long.txt", b"a" * (MAX_TEXT_READ_BYTES + 1))
        with pytest.raises(FileTooLargeError):
            await file_service.read_text("long.txt")

    async def test_reading_a_missing_file_is_not_found(self, file_service: FileService) -> None:
        with pytest.raises(StoredFileNotFoundError):
            await file_service.read_text("nothing.md")


class TestDeleting:
    async def test_a_deleted_file_is_gone(
        self, file_service: FileService, files_store: InMemoryObjectStore
    ) -> None:
        await file_service.upload("a.md", b"x")
        await file_service.delete("a.md")
        assert files_store.objects == {}

    async def test_deleting_what_is_not_there_says_so(self, file_service: FileService) -> None:
        """S3 answers a delete of nothing with success; a person deserves better."""
        with pytest.raises(StoredFileNotFoundError):
            await file_service.delete("nothing.md")

    async def test_a_reader_cannot_delete(self, file_service: FileService) -> None:
        await file_service.upload("a.md", b"x")
        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await file_service.delete("a.md")


async def test_without_a_store_every_use_case_says_storage_is_off() -> None:
    service = FileService(None, metadata=None)
    with pytest.raises(FileStorageUnavailableError):
        await service.list_folder()


class TestTheCatalogueBesideTheBucket:
    """What PostgreSQL records about a file — see docs/adr/0039."""

    async def test_an_upload_records_who_stored_it_and_what_it_holds(
        self, file_service: FileService, files_metadata: InMemoryFileMetadata
    ) -> None:
        await file_service.upload("inbox/a.md", b"# A", content_type="text/markdown")

        row = files_metadata.rows["inbox/a.md"]
        assert (row.size, row.content_type) == (3, "text/markdown")
        assert row.uploaded_by == an_editor().username
        assert row.uploaded_by_subject == an_editor().subject
        assert row.sha256 == sha256_of(b"# A")
        assert (row.created_at, row.updated_at) == (FIXED_NOW, FIXED_NOW)

    async def test_an_upload_carries_what_the_person_wrote_about_it(
        self, file_service: FileService, files_metadata: InMemoryFileMetadata
    ) -> None:
        await file_service.upload(
            "a.pdf",
            b"%PDF",
            details=FileDetails(title="Rapport 2026", description="Le bilan.", tags=("Budget",)),
        )

        assert files_metadata.rows["a.pdf"].details == FileDetails(
            title="Rapport 2026", description="Le bilan.", tags=("budget",)
        )

    async def test_an_upload_refuses_a_description_past_the_bound(
        self, file_service: FileService
    ) -> None:
        with pytest.raises(InvalidFileMetadataError):
            await file_service.upload("a.md", b"x", details=FileDetails(title="a" * 500))

    async def test_replacing_a_file_keeps_what_was_written_about_it(
        self, file_service: FileService, files_metadata: InMemoryFileMetadata
    ) -> None:
        await file_service.upload("a.md", b"one", details=FileDetails(title="Le contrat"))

        await file_service.upload("a.md", b"two", overwrite=True)

        row = files_metadata.rows["a.md"]
        assert row.details.title == "Le contrat"
        assert row.sha256 == sha256_of(b"two")

    async def test_replacing_a_file_and_describing_it_writes_the_new_description(
        self, file_service: FileService, files_metadata: InMemoryFileMetadata
    ) -> None:
        """Details given are what the file carries; details left out keep what was there."""
        await file_service.upload("a.md", b"one", details=FileDetails(title="Le contrat"))

        await file_service.upload(
            "a.md", b"two", overwrite=True, details=FileDetails(title="L'avenant")
        )

        assert files_metadata.rows["a.md"].details.title == "L'avenant"

    async def test_deleting_a_file_forgets_its_row(
        self, file_service: FileService, files_metadata: InMemoryFileMetadata
    ) -> None:
        await file_service.upload("a.md", b"x")

        await file_service.delete("a.md")

        assert files_metadata.rows == {}

    async def test_a_listing_carries_the_row_of_each_file(self, file_service: FileService) -> None:
        await file_service.upload("inbox/a.md", b"x", details=FileDetails(tags=("budget",)))

        listing = await file_service.list_folder("inbox")

        found = listing.files[0]
        assert found.key == "inbox/a.md"
        assert found.metadata is not None
        assert found.metadata.details.tags == ("budget",)

    async def test_a_file_written_straight_into_the_bucket_is_listed_undescribed(
        self, file_service: FileService, files_store: InMemoryObjectStore
    ) -> None:
        """The pipeline's own door: the bytes are what exist, the row is what is known."""
        files_store.objects["inbox/dropped.csv"] = (b"a,b\n", "text/csv")

        listing = await file_service.list_folder("inbox")

        assert [(f.key, f.metadata) for f in listing.files] == [("inbox/dropped.csv", None)]

    async def test_a_reader_reads_one_file_and_what_is_known_about_it(
        self, file_service: FileService
    ) -> None:
        await file_service.upload("a.md", b"x", details=FileDetails(title="Le contrat"))

        with acting_as(a_reader()):
            found = await file_service.describe("a.md")

        assert found.metadata is not None
        assert found.metadata.details.title == "Le contrat"
        assert found.size == 1

    async def test_describing_a_file_that_is_not_in_the_bucket_is_not_found(
        self, file_service: FileService
    ) -> None:
        with pytest.raises(StoredFileNotFoundError):
            await file_service.describe("nothing.md")


class TestWritingAboutAFile:
    async def test_an_editor_replaces_the_whole_description(
        self, file_service: FileService, files_metadata: InMemoryFileMetadata
    ) -> None:
        await file_service.upload("a.md", b"x", details=FileDetails(title="A", tags=("budget",)))

        await file_service.set_details("a.md", FileDetails(title="B", tags=("réseau",)))

        assert files_metadata.rows["a.md"].details == FileDetails(title="B", tags=("réseau",))

    async def test_a_reader_cannot(self, file_service: FileService) -> None:
        await file_service.upload("a.md", b"x")
        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await file_service.set_details("a.md", FileDetails(title="B"))

    async def test_a_file_that_is_not_in_the_bucket_cannot_be_described(
        self, file_service: FileService
    ) -> None:
        with pytest.raises(StoredFileNotFoundError):
            await file_service.set_details("nothing.md", FileDetails(title="B"))

    async def test_a_file_with_no_row_yet_gets_one(
        self, file_service: FileService, files_store: InMemoryObjectStore
    ) -> None:
        """Describing a file the pipeline dropped in is how it enters the catalogue."""
        files_store.objects["inbox/dropped.csv"] = (b"a,b\n", "text/csv")

        described = await file_service.set_details("inbox/dropped.csv", FileDetails(title="Export"))

        assert described.metadata is not None
        assert described.metadata.details.title == "Export"
        # Nobody uploaded it through this API, and the catalogue does not pretend.
        assert (described.metadata.uploaded_by, described.metadata.sha256) == ("", None)


class TestReconciling:
    async def test_it_records_a_row_for_every_object_the_bucket_holds(
        self, file_service: FileService, files_store: InMemoryObjectStore
    ) -> None:
        files_store.objects["inbox/a.csv"] = (b"a", "text/csv")
        files_store.objects["deep/down/b.csv"] = (b"b", "text/csv")

        report = await file_service.reconcile()

        assert report.recorded == 2
        assert await file_service.describe("deep/down/b.csv")

    async def test_it_forgets_a_row_whose_object_is_gone(
        self, file_service: FileService, files_store: InMemoryObjectStore
    ) -> None:
        """No foreign key can do this: the other side of the relation is a bucket."""
        await file_service.upload("a.md", b"x")
        files_store.objects.pop("a.md")

        report = await file_service.reconcile()

        assert (report.recorded, report.forgotten) == (0, 1)

    async def test_it_leaves_what_a_person_wrote_alone(
        self, file_service: FileService, files_metadata: InMemoryFileMetadata
    ) -> None:
        await file_service.upload("a.md", b"x", details=FileDetails(title="Le contrat"))

        await file_service.reconcile()

        assert files_metadata.rows["a.md"].details.title == "Le contrat"

    async def test_a_reader_cannot_reconcile(self, file_service: FileService) -> None:
        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await file_service.reconcile()


async def test_the_same_bytes_under_two_names_are_found_by_their_digest(
    file_service: FileService,
) -> None:
    """What a bucket cannot answer, and the reason the digest is recorded at all."""
    await file_service.upload("inbox/a.pdf", b"%PDF")
    await file_service.upload("archive/a-copy.pdf", b"%PDF")
    await file_service.upload("inbox/other.pdf", b"%OTHER")

    same = await file_service.copies_of("inbox/a.pdf")

    assert [found.key for found in same] == ["archive/a-copy.pdf"]


async def test_without_a_catalogue_a_file_is_still_stored_and_listed() -> None:
    """PostgreSQL is fatal at boot (docs/adr/0037), so this is a test seam only.

    It is stated all the same: the bucket is the source of truth for what
    exists, and the catalogue beside it never decides whether a file is there.
    """
    service = FileService(InMemoryObjectStore(), metadata=None)

    stored = await service.upload("a.md", b"x")
    listing = await service.list_folder()
    described = await service.set_details("a.md", FileDetails(title="A"))

    assert stored.metadata is None
    assert [found.key for found in listing.files] == ["a.md"]
    # Nothing to write it to, so nothing is claimed about it.
    assert described.metadata is None
    assert await service.copies_of("a.md") == ()
    assert await service.reconcile() == ReconcileReport(recorded=0, forgotten=0)
