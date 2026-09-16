"""The file metadata repository against a real PostgreSQL — see docs/adr/0039.

Everything else about file metadata is proved without a server. This is the one
place that proves the DDL applies, that the unique `object_key` really refuses
a second row for the same path, that the two upserts keep exactly what they
promise to keep, and that an array of accented tags survives the round trip.

It is the test that would have caught a `record` that blanked a description, a
`note_seen` that erased the uploader, or a `last_modified` column that dropped
its offset.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.files import FileDetails, FileMetadata, StoredFile
from ea.repositories.file_metadata_store import PostgresFileMetadataRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

FIXED_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
LATER = FIXED_NOW + timedelta(hours=1)


@pytest.fixture
def metadata_store(engine_at_head: AsyncEngine) -> PostgresFileMetadataRepository:
    return PostgresFileMetadataRepository(create_session_factory(engine_at_head))


def an_upload(
    key: str = "inbox/rapport.pdf",
    *,
    sha256: str | None = "a" * 64,
    details: FileDetails | None = None,
    uploaded_by: str = "nicolas",
    etag: str = "etag-1",
    size: int = 12,
) -> FileMetadata:
    return FileMetadata(
        id=uuid4(),
        key=key,
        size=size,
        content_type="application/pdf",
        etag=etag,
        sha256=sha256,
        last_modified=FIXED_NOW,
        details=details or FileDetails(),
        uploaded_by_subject="sub-1",
        uploaded_by=uploaded_by,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
    )


async def test_an_upload_comes_back_exactly_as_it_went_in(
    metadata_store: PostgresFileMetadataRepository,
) -> None:
    """Accented tags included — the column is an array of values, not a string."""
    details = FileDetails(title="Rapport 2026", description="Le bilan.", tags=("réseau", "budget"))

    await metadata_store.record(an_upload(details=details))
    read = await metadata_store.get("inbox/rapport.pdf")

    assert read is not None
    assert read.details == details
    assert read.uploaded_by == "nicolas"
    assert read.sha256 == "a" * 64
    # The offset survived: the column is `timestamptz`, not `timestamp`.
    assert read.last_modified == FIXED_NOW


async def test_replacing_a_file_keeps_what_a_person_wrote_about_it(
    metadata_store: PostgresFileMetadataRepository,
) -> None:
    """The point of the `DO UPDATE` set list: an upload never unwrites a description."""
    details = FileDetails(title="Rapport 2026", tags=("budget",))
    await metadata_store.record(an_upload(details=details))

    replaced = await metadata_store.record(
        an_upload(sha256="b" * 64, etag="etag-2", size=99, uploaded_by="claire")
    )

    assert replaced.details == details
    assert (replaced.size, replaced.sha256, replaced.uploaded_by) == (99, "b" * 64, "claire")
    # First seen once, and a second upload is not a first sighting.
    assert replaced.created_at == FIXED_NOW


async def test_there_is_one_row_per_path_however_often_it_is_written(
    metadata_store: PostgresFileMetadataRepository,
) -> None:
    await metadata_store.record(an_upload())
    await metadata_store.record(an_upload())
    await metadata_store.note_seen(
        StoredFile(
            key="inbox/rapport.pdf",
            size=12,
            last_modified=FIXED_NOW,
            content_type="application/pdf",
            etag="etag-1",
        ),
        now=LATER,
    )

    assert await metadata_store.all_keys() == ("inbox/rapport.pdf",)


class TestWhatAReconcileLearns:
    async def test_a_file_written_outside_the_api_gets_a_row_with_no_uploader(
        self, metadata_store: PostgresFileMetadataRepository
    ) -> None:
        seen = await metadata_store.note_seen(
            StoredFile(
                key="inbox/dropped.csv",
                size=7,
                last_modified=FIXED_NOW,
                content_type="text/csv",
                etag="etag-x",
            ),
            now=LATER,
        )

        assert (seen.uploaded_by, seen.sha256) == ("", None)
        assert seen.created_at == LATER

    async def test_it_never_blanks_the_uploader_of_a_file_the_api_stored(
        self, metadata_store: PostgresFileMetadataRepository
    ) -> None:
        """Why `note_seen` and `record` are two methods and not one flag."""
        await metadata_store.record(an_upload(details=FileDetails(title="Rapport 2026")))

        seen = await metadata_store.note_seen(
            StoredFile(
                key="inbox/rapport.pdf",
                size=12,
                last_modified=FIXED_NOW,
                content_type="application/pdf",
                etag="etag-1",
            ),
            now=LATER,
        )

        assert seen.uploaded_by == "nicolas"
        assert seen.details.title == "Rapport 2026"
        # Untouched object, so the digest still describes the bytes that are there.
        assert seen.sha256 == "a" * 64

    async def test_a_digest_that_no_longer_describes_the_bytes_is_dropped(
        self, metadata_store: PostgresFileMetadataRepository
    ) -> None:
        """A wrong digest answers "the same file?" wrongly; no digest says "unknown"."""
        await metadata_store.record(an_upload())

        seen = await metadata_store.note_seen(
            StoredFile(
                key="inbox/rapport.pdf",
                size=40,
                last_modified=LATER,
                content_type="application/pdf",
                etag="etag-2",
            ),
            now=LATER,
        )

        assert seen.sha256 is None
        assert (seen.size, seen.etag) == (40, "etag-2")


async def test_a_description_is_replaced_whole(
    metadata_store: PostgresFileMetadataRepository,
) -> None:
    await metadata_store.record(an_upload(details=FileDetails(title="A", tags=("budget",))))

    described = await metadata_store.describe(
        "inbox/rapport.pdf", FileDetails(title="B", tags=("réseau",)), now=LATER
    )

    assert described is not None
    assert described.details == FileDetails(title="B", tags=("réseau",))
    assert described.updated_at == LATER


async def test_describing_a_file_with_no_row_says_so(
    metadata_store: PostgresFileMetadataRepository,
) -> None:
    assert await metadata_store.describe("nothing.txt", FileDetails(), now=LATER) is None


async def test_a_row_is_forgotten_when_its_object_is_gone(
    metadata_store: PostgresFileMetadataRepository,
) -> None:
    await metadata_store.record(an_upload())

    assert await metadata_store.forget("inbox/rapport.pdf") is True
    assert await metadata_store.forget("inbox/rapport.pdf") is False
    assert await metadata_store.get("inbox/rapport.pdf") is None


async def test_a_whole_folder_is_one_query(
    metadata_store: PostgresFileMetadataRepository,
) -> None:
    await metadata_store.record(an_upload("inbox/a.md"))
    await metadata_store.record(an_upload("inbox/b.md"))

    found = await metadata_store.for_keys(["inbox/a.md", "inbox/b.md", "inbox/absent.md"])

    assert set(found) == {"inbox/a.md", "inbox/b.md"}
    assert await metadata_store.for_keys([]) == {}


async def test_the_same_bytes_under_two_names_are_found_by_the_digest(
    metadata_store: PostgresFileMetadataRepository,
) -> None:
    """The question a bucket cannot answer, and the reason `sha256` is indexed."""
    await metadata_store.record(an_upload("inbox/a.pdf", sha256="c" * 64))
    await metadata_store.record(an_upload("archive/a-copy.pdf", sha256="c" * 64))
    await metadata_store.record(an_upload("inbox/other.pdf", sha256="d" * 64))

    same = await metadata_store.with_digest("c" * 64)

    assert [found.key for found in same] == ["archive/a-copy.pdf", "inbox/a.pdf"]
