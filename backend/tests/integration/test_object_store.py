"""`MinioObjectStore` against a real S3 — the throwaway MinIO, never the Infra's.

No mocked driver: what is under test is what MinIO answers, including the error
codes `stat` translates and the way a delimiter turns keys into folders.
"""

from __future__ import annotations

import pytest

from ea.domain.errors import StoredFileNotFoundError
from ea.repositories.object_store import MinioObjectStore

pytestmark = pytest.mark.asyncio


async def test_a_stored_file_is_described_by_what_was_stored(minio_store: MinioObjectStore) -> None:
    stored = await minio_store.put("inbox/notes.md", b"# Notes\n", content_type="text/markdown")

    assert (stored.key, stored.size, stored.content_type) == ("inbox/notes.md", 8, "text/markdown")
    assert await minio_store.stat("inbox/notes.md") == stored


async def test_a_missing_key_is_none_not_an_error(minio_store: MinioObjectStore) -> None:
    assert await minio_store.stat("nothing/here.txt") is None


async def test_the_bytes_come_back_exactly_across_several_chunks(
    minio_store: MinioObjectStore,
) -> None:
    payload = bytes(range(256)) * 1024  # 256 KiB, several 64 KiB chunks
    await minio_store.put("big.bin", payload, content_type="application/octet-stream")

    stored, chunks = await minio_store.open("big.bin")

    assert stored.size == len(payload)
    assert b"".join([chunk async for chunk in chunks]) == payload


async def test_a_listing_shows_one_level_folders_first(minio_store: MinioObjectStore) -> None:
    for key in ("top.txt", "inbox/a.md", "inbox/sub/b.md"):
        await minio_store.put(key, b"x", content_type="text/plain")

    top = await minio_store.list_folder("", limit=1000)
    inbox = await minio_store.list_folder("inbox/", limit=1000)

    assert (top.folders, [f.key for f in top.files], top.truncated) == (
        ("inbox/",),
        ["top.txt"],
        False,
    )
    assert (inbox.folders, [f.key for f in inbox.files]) == (("inbox/sub/",), ["inbox/a.md"])


async def test_a_listing_beyond_the_limit_says_it_is_truncated(
    minio_store: MinioObjectStore,
) -> None:
    for index in range(3):
        await minio_store.put(f"many/{index}.txt", b"x", content_type="text/plain")

    listing = await minio_store.list_folder("many/", limit=2)

    assert (len(listing.files), listing.truncated) == (2, True)


async def test_a_deleted_file_cannot_be_opened(minio_store: MinioObjectStore) -> None:
    await minio_store.put("gone.txt", b"x", content_type="text/plain")

    await minio_store.delete("gone.txt")

    with pytest.raises(StoredFileNotFoundError):
        await minio_store.open("gone.txt")


async def test_the_probe_refuses_a_bucket_that_does_not_exist(
    minio_store: MinioObjectStore,
) -> None:
    other = MinioObjectStore(minio_store.client, "no-such-bucket-ea")
    with pytest.raises(RuntimeError, match="no-such-bucket-ea"):
        await other.probe()
