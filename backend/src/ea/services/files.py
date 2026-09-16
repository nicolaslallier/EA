"""Use cases over the files kept in MinIO — see docs/adr/0036 and docs/adr/0039.

The HTTP upload and the MCP tool both land here, so the rules no key can check
alone are stated once: a file already at a path is not replaced unless that was
asked for, and a file read as text must be text.

Since docs/adr/0039 there are two stores behind this one door, and which one
answers which question is the whole design. **MinIO says what exists** — a
listing, a download and a delete all go to the bucket, and a file it holds is a
file whether or not PostgreSQL has heard of it. **PostgreSQL says what is
known** — who uploaded it, when, what a person wrote about it, the digest of
its bytes. So a read never writes: a file the pipeline dropped into `inbox/`
comes back in a listing with no description rather than quietly acquiring a
row, and `reconcile()` is the catch-up that gives it one.

The two are written in that order, and the order matters: the object first,
the row second. A crash between them leaves a file in the bucket with nothing
recorded about it — which is exactly the state a file written by any other door
is in, and exactly what a reconcile repairs. The reverse order would leave a
row describing a file that was never stored, which nothing repairs.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

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
    CatalogedFile,
    CatalogedListing,
    FileDetails,
    FileMetadata,
    ReconcileReport,
    StoredFile,
    clean_details,
    clean_key,
    clean_prefix,
    sha256_of,
)
from ea.services.caller import require_caller, require_editor

if TYPE_CHECKING:
    from ea.domain.auth import Caller
    from ea.domain.ports import FileMetadataRepository, ObjectStore

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


logger = logging.getLogger(__name__)


class FileService:
    """The single entry point `api/` and `mcp/` use for the bucket and its catalogue.

    `store` is `None` on a deployment with `EA_S3_ENABLED` off: the service
    still exists, so every route and tool stays declared and answers 503.

    `metadata` has no default, on purpose. PostgreSQL is fatal at boot
    (docs/adr/0037), so a live deployment always has one and a `None` here is
    always a deliberate test seam — writing it down at the call site is what
    stops a forgotten wire-up from becoming a catalogue that silently records
    nothing.
    """

    def __init__(
        self,
        store: ObjectStore | None,
        *,
        metadata: FileMetadataRepository | None,
        clock: Clock = _utc_now,
    ) -> None:
        self._configured = store
        self._metadata = metadata
        self._now = clock

    def _store(self) -> ObjectStore:
        if self._configured is None:
            msg = "file storage is not configured on this deployment (EA_S3_ENABLED is off)"
            raise FileStorageUnavailableError(msg)
        return self._configured

    async def list_folder(self, prefix: str = "") -> CatalogedListing:
        """One folder of the bucket, each file paired with what is known about it.

        One query for the whole folder rather than a row lookup per file, which
        is why `file_metadata` mirrors the size and the type the bucket already
        reports — see docs/adr/0039.
        """
        require_caller()
        listing = await self._store().list_folder(clean_prefix(prefix), limit=MAX_LISTED_ENTRIES)
        known = await self._known([stored.key for stored in listing.files])
        return CatalogedListing(
            prefix=listing.prefix,
            folders=listing.folders,
            files=tuple(
                CatalogedFile(stored=stored, metadata=known.get(stored.key))
                for stored in listing.files
            ),
            truncated=listing.truncated,
        )

    async def describe(self, key: str) -> CatalogedFile:
        """One file, and its row when the catalogue holds one."""
        require_caller()
        path = clean_key(key)
        return CatalogedFile(stored=await self._found(path), metadata=await self._row(path))

    async def open(self, key: str) -> tuple[StoredFile, AsyncIterator[bytes]]:
        require_caller()
        return await self._store().open(clean_key(key))

    async def read_text(self, key: str) -> tuple[StoredFile, str]:
        """The file as UTF-8 text — for an agent, which has no use for a PDF's bytes."""
        require_caller()
        store = self._store()
        path = clean_key(key)
        found = await self._found(path)
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
        details: FileDetails | None = None,
    ) -> CatalogedFile:
        """Store a file, then record what was learned about it.

        `details` given are what the file carries afterwards; `details` left out
        keep whatever was already written, so replacing `rapport.pdf` does not
        unwrite the sentence somebody put beside it. That is two statements and
        not one — `record` never touches a description, by design — and the
        second is skipped entirely when there is nothing to say.

        The description is validated *before* a byte is written: refusing a
        4001-character description after the upload succeeded would leave the
        caller with a file stored and an error to read.
        """
        caller = require_editor()
        store = self._store()
        path = clean_key(key)
        wanted = clean_details(
            title=details.title if details else "",
            description=details.description if details else "",
            tags=details.tags if details else (),
        )
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
        return CatalogedFile(
            stored=stored,
            metadata=await self._record(stored, raw=raw, details=wanted, caller=caller),
        )

    async def set_details(self, key: str, details: FileDetails) -> CatalogedFile:
        """Replace what a person wrote about a file.

        The file has to be in the bucket: a description of nothing is a row
        `reconcile` would delete on its next run. A file with no row yet — one
        the pipeline or the console wrote — acquires one here, which is how it
        enters the catalogue without waiting for a reconcile.
        """
        require_editor()
        path = clean_key(key)
        stored = await self._found(path)
        wanted = clean_details(
            title=details.title, description=details.description, tags=details.tags
        )
        if self._metadata is None:
            return CatalogedFile(stored=stored)
        now = self._now()
        described = await self._metadata.describe(path, wanted, now=now)
        if described is None:
            await self._metadata.note_seen(stored, now=now)
            described = await self._metadata.describe(path, wanted, now=now)
        logger.info(
            "file %r described",
            path,
            extra={"action": "file_described", "key": path},
        )
        return CatalogedFile(stored=stored, metadata=described)

    async def delete(self, key: str) -> None:
        """Remove the object, then forget the row — in that order, for the same reason."""
        require_editor()
        store = self._store()
        path = clean_key(key)
        if await store.stat(path) is None:
            msg = f"no file at {path!r}"
            raise StoredFileNotFoundError(msg)
        await store.delete(path)
        if self._metadata is not None:
            await self._metadata.forget(path)
        logger.info("file %r deleted", path, extra={"action": "file_deleted", "key": path})

    async def copies_of(self, key: str) -> tuple[FileMetadata, ...]:
        """Every *other* file whose bytes are these exact bytes.

        The question a bucket cannot answer, and the only reason a digest is
        recorded at all. A file the API never received has no digest, so it has
        no copies here — an unknown, stated as an empty answer.
        """
        require_caller()
        path = clean_key(key)
        row = await self._row(path)
        if self._metadata is None or row is None or row.sha256 is None:
            return ()
        return tuple(
            found for found in await self._metadata.with_digest(row.sha256) if found.key != path
        )

    async def reconcile(self) -> ReconcileReport:
        """Walk the whole bucket and make the catalogue agree with it.

        The catch-up for the one way these two stores drift: a file written by
        a door that is not this API — the `alimenter-catalogue` pipeline, `mc`,
        the MinIO console — and a file removed the same way. Running it twice is
        harmless, and it never touches what a person wrote.
        """
        require_editor()
        if self._metadata is None:
            return ReconcileReport(recorded=0, forgotten=0)
        store = self._store()
        now = self._now()
        seen: set[str] = set()
        recorded = 0
        async for stored in store.walk():
            seen.add(stored.key)
            known = await self._metadata.get(stored.key)
            await self._metadata.note_seen(stored, now=now)
            if known is None:
                recorded += 1
        forgotten = 0
        for key in await self._metadata.all_keys():
            if key not in seen and await self._metadata.forget(key):
                forgotten += 1
        logger.info(
            "file catalogue reconciled: %d recorded, %d forgotten",
            recorded,
            forgotten,
            extra={"action": "files_reconciled", "recorded": recorded, "forgotten": forgotten},
        )
        return ReconcileReport(recorded=recorded, forgotten=forgotten)

    async def _found(self, path: str) -> StoredFile:
        """The object at `path`, or the 404 that says the bucket has none."""
        stored = await self._store().stat(path)
        if stored is None:
            msg = f"no file at {path!r}"
            raise StoredFileNotFoundError(msg)
        return stored

    async def _row(self, path: str) -> FileMetadata | None:
        return None if self._metadata is None else await self._metadata.get(path)

    async def _known(self, keys: list[str]) -> dict[str, FileMetadata]:
        return {} if self._metadata is None else await self._metadata.for_keys(keys)

    async def _record(
        self, stored: StoredFile, *, raw: bytes, details: FileDetails, caller: Caller
    ) -> FileMetadata | None:
        if self._metadata is None:
            return None
        now = self._now()
        recorded = await self._metadata.record(
            FileMetadata(
                id=uuid4(),
                key=stored.key,
                size=stored.size,
                content_type=stored.content_type,
                etag=stored.etag,
                sha256=sha256_of(raw),
                last_modified=stored.last_modified,
                details=details,
                uploaded_by_subject=caller.subject,
                uploaded_by=caller.username,
                created_at=now,
                updated_at=now,
            )
        )
        if details == FileDetails() or recorded.details == details:
            return recorded
        # `record` never touches a description, so saying something about a file
        # while uploading it is a second, deliberate statement — and the only
        # thing that distinguishes "I am also describing it" from "I am
        # replacing the bytes and leaving the description alone".
        return await self._metadata.describe(stored.key, details, now=now) or recorded
