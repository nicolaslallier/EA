"""The `ObjectStore` port over MinIO — see docs/adr/0036.

The `minio` SDK is synchronous, so every call runs in a worker thread: done on
the event loop, a 50 MB upload would stall every other request, `/mcp` included.
A download is streamed in chunks, each read in a thread too, so a large file is
never held whole by this process.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Final

import urllib3
from minio import Minio
from minio.error import S3Error

from ea.core.config import Settings
from ea.domain.errors import StoredFileNotFoundError
from ea.domain.files import FileListing, StoredFile, guess_content_type

CHUNK_BYTES: Final = 64 * 1024
#: What MinIO calls a key that is not there, depending on the verb.
_MISSING: Final = frozenset({"NoSuchKey", "NoSuchObject"})
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


def minio_client(settings: Settings) -> Minio:
    """A client for `s3_endpoint`, trusting `s3_ca_cert` when there is one.

    Same pool as `pipelines/storage.py`, with the CA swapped: a bare
    `PoolManager` would drop the SDK's timeouts and its retries on a 5xx.
    """
    http_client = None
    if settings.s3_ca_cert:
        http_client = urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=10, read=300),
            maxsize=10,
            cert_reqs="CERT_REQUIRED",
            ca_certs=settings.s3_ca_cert,
            retries=urllib3.Retry(
                total=5, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504]
            ),
        )
    return Minio(
        settings.s3_endpoint,
        access_key=settings.s3_access_key.get_secret_value(),
        secret_key=settings.s3_secret_key.get_secret_value(),
        secure=settings.s3_secure,
        http_client=http_client,
        # The Infra MinIO sets no `MINIO_REGION`, and without one minio-py
        # issues a `GetBucketLocation` call before the first bucket call —
        # including the boot `probe()` — which the documented policy (ADR
        # 0036) does not grant, so boot would fail with `AccessDenied`. Fixing
        # it also saves that round trip on every call.
        region="us-east-1",
    )


class MinioObjectStore:
    def __init__(self, client: Minio, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    async def probe(self) -> None:
        """Refuse to boot on a bucket that is not there, rather than 500 on the first upload."""
        if not await asyncio.to_thread(self.client.bucket_exists, self.bucket):
            msg = f"the bucket {self.bucket!r} does not exist on this MinIO"
            raise RuntimeError(msg)

    async def list_folder(self, prefix: str, *, limit: int) -> FileListing:
        return await asyncio.to_thread(self._list_folder, prefix, limit)

    def _list_folder(self, prefix: str, limit: int) -> FileListing:
        folders: list[str] = []
        files: list[StoredFile] = []
        for found in self.client.list_objects(self.bucket, prefix=prefix or None, recursive=False):
            if len(folders) + len(files) == limit:
                return FileListing(prefix, tuple(folders), tuple(files), truncated=True)
            name = found.object_name or ""
            if name == prefix:
                # The folder marker itself, e.g. `inbox/` written as an object
                # by `mc`/the MinIO console — not a nameless sub-folder.
                continue
            if found.is_dir:
                folders.append(name)
            else:
                files.append(
                    StoredFile(
                        key=name,
                        size=found.size or 0,
                        last_modified=found.last_modified or _EPOCH,
                        content_type=guess_content_type(name),
                    )
                )
        return FileListing(prefix, tuple(folders), tuple(files), truncated=False)

    async def stat(self, key: str) -> StoredFile | None:
        try:
            found = await asyncio.to_thread(self.client.stat_object, self.bucket, key)
        except S3Error as error:
            if error.code in _MISSING:
                return None
            raise
        return StoredFile(
            key=key,
            size=found.size or 0,
            last_modified=found.last_modified or _EPOCH,
            content_type=found.content_type or guess_content_type(key),
        )

    async def put(self, key: str, data: bytes, *, content_type: str) -> StoredFile:
        await asyncio.to_thread(
            self.client.put_object,
            self.bucket,
            key,
            io.BytesIO(data),
            len(data),
            content_type=content_type,
        )
        stored = await self.stat(key)
        if stored is None:  # pragma: no cover - deleted between the two calls
            msg = f"no file at {key!r}"
            raise StoredFileNotFoundError(msg)
        return stored

    async def open(self, key: str) -> tuple[StoredFile, AsyncIterator[bytes]]:
        missing = f"no file at {key!r}"
        stored = await self.stat(key)
        if stored is None:
            raise StoredFileNotFoundError(missing)
        try:
            response = await asyncio.to_thread(self.client.get_object, self.bucket, key)
        except S3Error as error:
            # Deleted between `stat` and `get_object`.
            if error.code in _MISSING:
                raise StoredFileNotFoundError(missing) from None
            raise
        return stored, _chunks(response)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.remove_object, self.bucket, key)


async def _chunks(response: urllib3.BaseHTTPResponse) -> AsyncIterator[bytes]:
    try:
        while chunk := await asyncio.to_thread(response.read, CHUNK_BYTES):
            yield chunk
    finally:
        response.close()
        response.release_conn()
