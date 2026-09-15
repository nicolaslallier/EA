"""The file use cases, over the in-memory store: the rules, not MinIO."""

from __future__ import annotations

import pytest

from ea.domain.errors import (
    FileNotTextError,
    FileStorageUnavailableError,
    FileTooLargeError,
    InvalidFileKeyError,
    NotAuthorisedError,
    StoredFileExistsError,
    StoredFileNotFoundError,
)
from ea.domain.files import MAX_FILE_BYTES, MAX_TEXT_READ_BYTES
from ea.services.caller import acting_as
from ea.services.files import FileService
from tests.conftest import InMemoryObjectStore, a_reader

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
    service = FileService(None)
    with pytest.raises(FileStorageUnavailableError):
        await service.list_folder()
