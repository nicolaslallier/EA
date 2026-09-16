"""The table describing the files kept in the MinIO bucket — see docs/adr/0039.

The bytes live in MinIO and nothing here holds them. This row holds what the
bucket cannot answer — who uploaded a file, when this catalogue first saw it,
what a person wrote about it, the digest of its content — and mirrors what the
bucket *does* answer, so that listing a folder of a thousand files costs one
query rather than a thousand `stat` calls.

Three things about it are decisions.

**`object_key` is unique, and it is the only handle.** A file is identified by
where it is in the bucket, because that is the one name MinIO and PostgreSQL
share; the `id` is a surrogate for clients, never a second way to find a file.
The constraint rather than a read-then-insert: two uploads of the same path in
the same instant both look, and only one may write.

**There is no foreign key, and there cannot be.** The other side of this
relation is an object in MinIO, which PostgreSQL has nothing to reference. A
row whose object was removed outside the API is therefore possible, and it is
the reconcile of `ea.files_reindex` that removes it — never a cascade.

**`sha256` is nullable on purpose.** It is known for a file this API received
and unknown for one the pipeline or the MinIO console wrote, which a reconcile
picks up from a listing without reading a byte. `NULL` says "not computed";
an empty string would say "computed, and empty", which is a real digest of no
bytes and a different fact.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import ARRAY, BigInteger, DateTime, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from ea.db.base import Base
from ea.domain.files import SHA256_LENGTH


class FileMetadataRecord(Base):
    """What is known about one object of the `ea-catalogue` bucket."""

    __tablename__ = "file_metadata"
    __table_args__ = (UniqueConstraint("object_key"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    #: The full path in the bucket, folders included. `Text` and not a bounded
    #: `String`: S3 caps a key at 1024 *bytes*, which no character count states,
    #: and `domain/files.clean_key` is what enforces it.
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    # `BigInteger`: the domain caps an upload at 50 MB, but a file the pipeline
    # or the console wrote is bounded by the bucket, not by us, and a 4-byte
    # integer stops at 2 GB.
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    #: S3's change marker, mirrored so a reconcile can tell a replaced object
    #: from an untouched one without reading it.
    etag: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    sha256: Mapped[str | None] = mapped_column(String(SHA256_LENGTH), nullable=True, index=True)
    #: When the bucket says the object was last written — mirrored, so a listing
    #: needs no `stat` per row.
    last_modified: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # An array and not a joined string, for the same reason `heading_path` is
    # one: a tag is a value, and `LIKE '%réseau%'` would match `réseau-lent`.
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    #: The Keycloak subject of the uploader, and the username to show beside it.
    #: The subject is what is stable; the username is what a person reads, and
    #: it is copied here rather than looked up, because Keycloak is not a table
    #: this database can join against.
    uploaded_by_subject: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    uploaded_by: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    #: When this catalogue first saw the file, and when this row last changed —
    #: neither of which is `last_modified`, which belongs to the object.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
