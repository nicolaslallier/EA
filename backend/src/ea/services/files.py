"""Use cases over the files kept in MinIO — see docs/adr/0036.

The HTTP upload and the MCP tool both land here, so the two rules no key can
check alone are stated once: a file already at a path is not replaced unless
that was asked for, and a file read as text must be text.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from ea.domain.errors import (
    FileNotTextError,
    FileStorageUnavailableError,
    FileTooLargeError,
    StoredFileExistsError,
    StoredFileNotFoundError,
)
from ea.domain.files import (
    DEFAULT_CONTENT_TYPE,
    MAX_FILE_BYTES,
    MAX_LISTED_ENTRIES,
    MAX_TEXT_READ_BYTES,
    FileListing,
    StoredFile,
    clean_key,
    clean_prefix,
)
from ea.services.caller import require_caller, require_editor

if TYPE_CHECKING:
    from ea.domain.ports import ObjectStore

logger = logging.getLogger(__name__)


class FileService:
    """The single entry point `api/` and `mcp/` use for the bucket.

    `store` is `None` on a deployment with `EA_S3_ENABLED` off: the service
    still exists, so every route and tool stays declared and answers 503.
    """

    def __init__(self, store: ObjectStore | None) -> None:
        self._configured = store

    def _store(self) -> ObjectStore:
        if self._configured is None:
            msg = "file storage is not configured on this deployment (EA_S3_ENABLED is off)"
            raise FileStorageUnavailableError(msg)
        return self._configured

    async def list_folder(self, prefix: str = "") -> FileListing:
        require_caller()
        return await self._store().list_folder(clean_prefix(prefix), limit=MAX_LISTED_ENTRIES)

    async def open(self, key: str) -> tuple[StoredFile, AsyncIterator[bytes]]:
        require_caller()
        return await self._store().open(clean_key(key))

    async def read_text(self, key: str) -> tuple[StoredFile, str]:
        """The file as UTF-8 text — for an agent, which has no use for a PDF's bytes."""
        require_caller()
        store = self._store()
        path = clean_key(key)
        found = await store.stat(path)
        if found is None:
            msg = f"no file at {path!r}"
            raise StoredFileNotFoundError(msg)
        if found.size > MAX_TEXT_READ_BYTES:
            msg = f"{path!r} is {found.size} bytes; at most {MAX_TEXT_READ_BYTES} are read as text"
            raise FileTooLargeError(msg)
        stored, chunks = await store.open(path)
        raw = b"".join([chunk async for chunk in chunks])
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            msg = f"{path!r} is not UTF-8 text; a person can download it from the web interface"
            raise FileNotTextError(msg) from None
        return stored, text

    async def upload(
        self,
        key: str,
        raw: bytes,
        *,
        content_type: str = DEFAULT_CONTENT_TYPE,
        overwrite: bool = False,
    ) -> StoredFile:
        require_editor()
        store = self._store()
        path = clean_key(key)
        if len(raw) > MAX_FILE_BYTES:
            msg = f"a file is at most {MAX_FILE_BYTES // (1024 * 1024)} MB"
            raise FileTooLargeError(msg)
        # ponytail: stat-then-put — two editors writing one path in the same
        # instant both succeed, the last one wins. minio's put_object exposes no
        # If-None-Match; switch to a conditional write if that race ever matters.
        if not overwrite and await store.stat(path) is not None:
            msg = f"a file already exists at {path!r}; upload it with overwrite to replace it"
            raise StoredFileExistsError(msg)
        stored = await store.put(path, raw, content_type=content_type)
        logger.info(
            "file %r stored (%d bytes)",
            stored.key,
            stored.size,
            extra={"action": "file_stored", "key": stored.key},
        )
        return stored

    async def delete(self, key: str) -> None:
        require_editor()
        store = self._store()
        path = clean_key(key)
        if await store.stat(path) is None:
            msg = f"no file at {path!r}"
            raise StoredFileNotFoundError(msg)
        await store.delete(path)
        logger.info("file %r deleted", path, extra={"action": "file_deleted", "key": path})
