"""The PostgreSQL implementation of `FileMetadataRepository` — see docs/adr/0039.

It takes the *session factory*, like the document and diagram stores, and opens
one unit of work per call: every use case here is a single write, so a session
per call is a transaction per use case.

Both writes are `INSERT ... ON CONFLICT (object_key) DO UPDATE`, and that is
the whole design. "Is there already a row for this path?" followed by an insert
is two statements with a gap in the middle, and the gap is exactly where a
second upload of the same path lands; the upsert asks the question and answers
it inside one statement, which the unique constraint makes race-free.

What the `DO UPDATE` leaves alone is as deliberate as what it sets. The title,
the description and the tags are never touched: replacing a file does not
unwrite what a person said about it. `created_at` is never touched either — it
records when this catalogue first saw the file, which a second upload does not
change.

Every value is bound: these are SQLAlchemy constructs, compiled to parameters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from sqlalchemy import and_, case, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult

from ea.db.models.file import FileMetadataRecord
from ea.domain.files import FileDetails, FileMetadata, StoredFile

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _rows_affected(result: object) -> int:
    """How many rows a DELETE touched — see `document_store._rows_affected`."""
    return cast("CursorResult[Any]", result).rowcount


def _to_metadata(row: FileMetadataRecord) -> FileMetadata:
    return FileMetadata(
        id=row.id,
        key=row.object_key,
        size=row.byte_size,
        content_type=row.content_type,
        etag=row.etag,
        sha256=row.sha256,
        last_modified=row.last_modified,
        details=FileDetails(
            title=row.title,
            description=row.description,
            tags=tuple(row.tags),
        ),
        uploaded_by_subject=row.uploaded_by_subject,
        uploaded_by=row.uploaded_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresFileMetadataRepository:
    """What is known about the objects of the bucket, beside the bucket."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def record(self, metadata: FileMetadata) -> FileMetadata:
        """Store what an upload learned: the facts, the digest and the uploader.

        The description is left as it was, so overwriting `rapport.pdf` keeps
        the sentence somebody wrote about it. It is deliberately *not* cleared
        along with the digest: a stale digest is a wrong answer, a description
        of a replaced file is a human's problem to notice and edit.
        """
        statement = insert(FileMetadataRecord).values(
            id=metadata.id,
            object_key=metadata.key,
            byte_size=metadata.size,
            content_type=metadata.content_type,
            etag=metadata.etag,
            sha256=metadata.sha256,
            last_modified=metadata.last_modified,
            title=metadata.details.title,
            description=metadata.details.description,
            tags=list(metadata.details.tags),
            uploaded_by_subject=metadata.uploaded_by_subject,
            uploaded_by=metadata.uploaded_by,
            created_at=metadata.created_at,
            updated_at=metadata.updated_at,
        )
        # `returning` is applied *after* the conflict clause: on an insert that
        # became an update, it is the stored row that comes back, description
        # and `created_at` included — never the values proposed above.
        upsert = statement.on_conflict_do_update(
            index_elements=[FileMetadataRecord.object_key],
            set_={
                "byte_size": statement.excluded.byte_size,
                "content_type": statement.excluded.content_type,
                "etag": statement.excluded.etag,
                "sha256": statement.excluded.sha256,
                "last_modified": statement.excluded.last_modified,
                "uploaded_by_subject": statement.excluded.uploaded_by_subject,
                "uploaded_by": statement.excluded.uploaded_by,
                "updated_at": statement.excluded.updated_at,
            },
        ).returning(FileMetadataRecord)
        async with self._sessions.begin() as session:
            row = (await session.execute(upsert)).scalar_one()
            return _to_metadata(row)

    async def note_seen(self, stored: StoredFile, *, now: datetime) -> FileMetadata:
        """Store what a listing learned about an object, and only that.

        The uploader stays as it was — a reconcile never met one — and so does
        the digest, *unless* the `etag` says the object was replaced since it
        was computed. Then it is dropped: it described bytes that are gone, and
        this catch-up has a listing, not the file, so it cannot compute another.
        """
        statement = insert(FileMetadataRecord).values(
            id=uuid4(),
            object_key=stored.key,
            byte_size=stored.size,
            content_type=stored.content_type,
            etag=stored.etag,
            sha256=None,
            last_modified=stored.last_modified,
            created_at=now,
            updated_at=now,
        )
        upsert = statement.on_conflict_do_update(
            index_elements=[FileMetadataRecord.object_key],
            set_={
                "byte_size": statement.excluded.byte_size,
                "content_type": statement.excluded.content_type,
                "etag": statement.excluded.etag,
                # The digest is kept only while the `etag` proves the object
                # was not replaced. An empty `etag` proves nothing — a store
                # that reports none makes every comparison trivially true — so
                # it drops the digest rather than vouching for it.
                "sha256": case(
                    (
                        and_(
                            FileMetadataRecord.etag != "",
                            FileMetadataRecord.etag == statement.excluded.etag,
                        ),
                        FileMetadataRecord.sha256,
                    ),
                    else_=None,
                ),
                "last_modified": statement.excluded.last_modified,
                "updated_at": statement.excluded.updated_at,
            },
        ).returning(FileMetadataRecord)
        async with self._sessions.begin() as session:
            row = (await session.execute(upsert)).scalar_one()
            return _to_metadata(row)

    async def get(self, key: str) -> FileMetadata | None:
        statement = select(FileMetadataRecord).where(FileMetadataRecord.object_key == key)
        async with self._sessions() as session:
            row = (await session.execute(statement)).scalar_one_or_none()
            return _to_metadata(row) if row is not None else None

    async def for_keys(self, keys: Sequence[str]) -> dict[str, FileMetadata]:
        """One query for a whole folder, which is why the facts are mirrored here."""
        if not keys:
            return {}
        statement = select(FileMetadataRecord).where(FileMetadataRecord.object_key.in_(list(keys)))
        async with self._sessions() as session:
            rows = (await session.execute(statement)).scalars()
            return {row.object_key: _to_metadata(row) for row in rows}

    async def describe(
        self, key: str, details: FileDetails, *, now: datetime
    ) -> FileMetadata | None:
        """Replace what a person wrote about a file, or `None` when no row holds it."""
        async with self._sessions.begin() as session:
            row = (
                await session.execute(
                    select(FileMetadataRecord).where(FileMetadataRecord.object_key == key)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            row.title = details.title
            row.description = details.description
            row.tags = list(details.tags)
            row.updated_at = now
            await session.flush()
            return _to_metadata(row)

    async def forget(self, key: str) -> bool:
        async with self._sessions.begin() as session:
            removed = await session.execute(
                delete(FileMetadataRecord).where(FileMetadataRecord.object_key == key)
            )
        return bool(_rows_affected(removed))

    async def all_keys(self) -> tuple[str, ...]:
        """Every path this catalogue holds a row for, in bucket order."""
        statement = select(FileMetadataRecord.object_key).order_by(FileMetadataRecord.object_key)
        async with self._sessions() as session:
            return tuple((await session.execute(statement)).scalars())

    async def with_digest(self, sha256: str) -> tuple[FileMetadata, ...]:
        """Every file whose bytes hash to this — the index on `sha256` is for exactly this."""
        statement = (
            select(FileMetadataRecord)
            .where(FileMetadataRecord.sha256 == sha256)
            .order_by(FileMetadataRecord.object_key)
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).scalars()
            return tuple(_to_metadata(row) for row in rows)
