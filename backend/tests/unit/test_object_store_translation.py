"""`MinioObjectStore`'s translation of the `minio` SDK, driven by a hand-written stub.

What a real MinIO answers — S3 error codes, the shape of a listing — is proved
against a real server in `tests/integration/test_object_store.py`, which needs
Docker. What is under test here is the *translation*: a `NoSuchKey` becomes
`None` or `StoredFileNotFoundError` depending on the call, `is_dir` becomes a
folder, and a missing bucket fails `probe` loudly. No mock library: the `Minio`
surface this store touches is five methods, and a hand-written double reads as
what MinIO actually returns.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import IO

import pytest
from minio.datatypes import Object as MinioObject
from minio.error import S3Error

from ea.core.config import Settings
from ea.domain.errors import StoredFileNotFoundError
from ea.repositories.object_store import MinioObjectStore, minio_client

_A_MOMENT = datetime(2026, 1, 1, tzinfo=UTC)


def _s3_error(code: str) -> S3Error:
    return S3Error(None, code, code, None, None, None)  # type: ignore[arg-type]


class _StubBody:
    """What `get_object` returns: `.read(n)` then empty, like `urllib3`'s response."""

    def __init__(self, data: bytes) -> None:
        self._remaining = data
        self.closed = False
        self.released = False

    def read(self, size: int) -> bytes:
        chunk, self._remaining = self._remaining[:size], self._remaining[size:]
        return chunk

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


class StubMinioClient:
    """Just enough of `Minio` to drive the store's translation."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}
        self.listing: list[MinioObject] = []
        self.bucket_exists_result = True
        self.removed: list[str] = []

    def bucket_exists(self, bucket_name: str) -> bool:
        return self.bucket_exists_result

    def stat_object(self, bucket_name: str, object_name: str) -> MinioObject:
        if object_name not in self.objects:
            raise _s3_error("NoSuchKey")
        return MinioObject(
            bucket_name,
            object_name,
            last_modified=_A_MOMENT,
            size=len(self.objects[object_name]),
            content_type=self.content_types.get(object_name),
        )

    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: IO[bytes],
        length: int,
        content_type: str = "application/octet-stream",
    ) -> None:
        self.objects[object_name] = data.read(length)
        self.content_types[object_name] = content_type

    def get_object(self, bucket_name: str, object_name: str) -> _StubBody:
        if object_name not in self.objects:
            raise _s3_error("NoSuchKey")
        return _StubBody(self.objects[object_name])

    def remove_object(self, bucket_name: str, object_name: str) -> None:
        self.objects.pop(object_name, None)
        self.removed.append(object_name)

    def list_objects(
        self, bucket_name: str, prefix: str | None = None, recursive: bool = False
    ) -> Iterator[MinioObject]:
        return iter(self.listing)


def _store() -> tuple[MinioObjectStore, StubMinioClient]:
    client = StubMinioClient()
    return MinioObjectStore(client, "ea-test"), client  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_missing_key_is_none_not_an_error() -> None:
    store, _ = _store()

    assert await store.stat("nothing/here.txt") is None


@pytest.mark.asyncio
async def test_another_s3_error_code_is_not_swallowed() -> None:
    store, client = _store()

    def _raise_access_denied(bucket_name: str, object_name: str) -> MinioObject:
        raise _s3_error("AccessDenied")

    client.stat_object = _raise_access_denied  # type: ignore[method-assign]

    with pytest.raises(S3Error, match="AccessDenied"):
        await store.stat("secret.txt")


@pytest.mark.asyncio
async def test_is_dir_entries_become_folders_and_the_rest_become_files() -> None:
    store, client = _store()
    client.listing = [
        MinioObject("ea-test", "inbox/"),
        MinioObject("ea-test", "top.txt", size=3, last_modified=_A_MOMENT),
    ]

    listing = await store.list_folder("", limit=1000)

    assert listing.folders == ("inbox/",)
    assert [f.key for f in listing.files] == ["top.txt"]
    assert listing.truncated is False


@pytest.mark.asyncio
async def test_an_entry_equal_to_the_prefix_is_not_a_nameless_folder() -> None:
    """`mc`/the MinIO console can write the folder marker itself as an object
    whose key is the prefix (`inbox/`) — that must not surface as a folder with
    no name (Minor 8 of the final review)."""
    store, client = _store()
    client.listing = [
        MinioObject("ea-test", "inbox/"),
        MinioObject("ea-test", "inbox/sub/"),
    ]

    listing = await store.list_folder("inbox/", limit=1000)

    assert listing.folders == ("inbox/sub/",)


@pytest.mark.asyncio
async def test_a_listing_beyond_the_limit_is_marked_truncated() -> None:
    store, client = _store()
    client.listing = [
        MinioObject("ea-test", f"many/{index}.txt", size=1, last_modified=_A_MOMENT)
        for index in range(3)
    ]

    listing = await store.list_folder("many/", limit=2)

    assert len(listing.files) == 2
    assert listing.truncated is True


@pytest.mark.asyncio
async def test_the_probe_raises_when_the_bucket_is_missing() -> None:
    store, client = _store()
    client.bucket_exists_result = False

    with pytest.raises(RuntimeError, match="ea-test"):
        await store.probe()


@pytest.mark.asyncio
async def test_the_probe_passes_when_the_bucket_is_there() -> None:
    store, _ = _store()

    await store.probe()  # does not raise


@pytest.mark.asyncio
async def test_put_stores_and_stat_reports_it_back() -> None:
    store, client = _store()

    stored = await store.put("notes.md", b"# Notes\n", content_type="text/markdown")

    assert (stored.key, stored.size, stored.content_type) == ("notes.md", 8, "text/markdown")
    assert client.objects["notes.md"] == b"# Notes\n"


@pytest.mark.asyncio
async def test_open_streams_the_stored_bytes_and_closes_the_response() -> None:
    store, _ = _store()
    await store.put("big.bin", b"0123456789", content_type="application/octet-stream")

    stored, chunks = await store.open("big.bin")

    assert stored.size == 10
    assert b"".join([chunk async for chunk in chunks]) == b"0123456789"


@pytest.mark.asyncio
async def test_open_on_a_missing_key_raises_stored_file_not_found() -> None:
    store, _ = _store()

    with pytest.raises(StoredFileNotFoundError):
        await store.open("nothing.txt")


@pytest.mark.asyncio
async def test_open_when_the_object_vanishes_between_stat_and_get_still_raises_not_found() -> None:
    """The race `open`'s docstring calls out: gone between the two calls."""
    store, client = _store()
    client.objects["flaky.txt"] = b"x"

    def _raise_no_such_key(bucket_name: str, object_name: str) -> _StubBody:
        raise _s3_error("NoSuchKey")

    client.get_object = _raise_no_such_key  # type: ignore[method-assign]

    with pytest.raises(StoredFileNotFoundError):
        await store.open("flaky.txt")


@pytest.mark.asyncio
async def test_delete_removes_the_object() -> None:
    store, client = _store()
    client.objects["gone.txt"] = b"x"

    await store.delete("gone.txt")

    assert client.removed == ["gone.txt"]


def test_minio_client_builds_without_a_ca_cert() -> None:
    settings = Settings(debug=True, s3_enabled=True, s3_access_key="ea-api", s3_secret_key="secret")

    assert minio_client(settings) is not None


def test_minio_client_is_built_with_a_fixed_region() -> None:
    """Without a region, minio-py issues `GetBucketLocation` before the first
    bucket call — including the boot `probe()` — and the Infra MinIO's policy
    (Important 2 of the final review) does not grant it, so boot would fail
    with `AccessDenied`. `region` is a private attribute of `Minio`'s
    `_base_url` (see `minio.api.Minio._get_region`); there is no public one to
    assert on instead."""
    settings = Settings(debug=True, s3_enabled=True, s3_access_key="ea-api", s3_secret_key="secret")

    client = minio_client(settings)

    assert client._base_url.region == "us-east-1"


def test_minio_client_trusts_the_ca_cert_when_there_is_one() -> None:
    settings = Settings(
        debug=True,
        s3_enabled=True,
        s3_access_key="ea-api",
        s3_secret_key="secret",
        s3_ca_cert="/etc/ssl/certs/infra-ca.crt",
    )

    assert minio_client(settings) is not None
