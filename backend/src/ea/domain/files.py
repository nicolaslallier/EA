"""What a stored file is, which paths it may live under, and what is known about it.

Pure: the bucket itself is `repositories/object_store.py`'s business. A folder
is not a stored thing in S3 — it is the shared start of some keys — so a
listing *computes* folders from keys and nothing here ever writes one. See
docs/adr/0036.

The metadata of docs/adr/0039 lives here too, and the split is the whole point:
a `StoredFile` is what the bucket knows about an object, a `FileMetadata` is the
row PostgreSQL keeps beside it, and a `CatalogedFile` is the two put together.
The bucket stays the source of truth for *what exists* — an object with no row
is a file, listed, merely undescribed.
"""

from __future__ import annotations

import hashlib
import mimetypes
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final
from uuid import UUID

from ea.domain.errors import InvalidFileKeyError, InvalidFileMetadataError

#: The largest upload. The HTTP adapter reads a body under this cap, so it is
#: also the most one request makes this process hold.
MAX_FILE_BYTES: Final = 50 * 1024 * 1024
#: The largest file an agent reads as text: an answer, not a download.
MAX_TEXT_READ_BYTES: Final = 1024 * 1024
#: The most entries one listing returns; beyond it `truncated` says so.
MAX_LISTED_ENTRIES: Final = 1000
#: S3's own limit on a key, in UTF-8 bytes.
MAX_KEY_BYTES: Final = 1024
DEFAULT_CONTENT_TYPE: Final = "application/octet-stream"

#: The bounds on what a person writes about a file — see docs/adr/0039.
MAX_FILE_TITLE_LENGTH: Final = 200
MAX_FILE_DESCRIPTION_LENGTH: Final = 4000
MAX_FILE_TAGS: Final = 20
MAX_TAG_LENGTH: Final = 50
#: The width of a hex sha256, which is what the digest column holds.
SHA256_LENGTH: Final = 64

# Register markdown MIME type for the domain (later tasks rely on it)
mimetypes.add_type("text/markdown", ".md")
mimetypes.add_type("text/markdown", ".markdown")


@dataclass(frozen=True, slots=True)
class StoredFile:
    """What the bucket knows about one object, and nothing more."""

    key: str
    size: int
    last_modified: datetime
    content_type: str
    #: S3's own change marker. Defaulted because a store that does not report
    #: one is still a store; it is a hint for reconciliation, never an identity.
    etag: str = ""

    @property
    def name(self) -> str:
        return self.key.rsplit("/", 1)[-1]


@dataclass(frozen=True, slots=True)
class FileListing:
    prefix: str
    folders: tuple[str, ...]
    files: tuple[StoredFile, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class FileDetails:
    """What a person says about a file — the half no bucket can guess.

    Empty strings rather than `None`: "nothing was written here" and "the empty
    string was written here" are the same fact about a title, and two spellings
    of it would be two states for every client to tell apart.
    """

    title: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FileMetadata:
    """The row PostgreSQL keeps about one object of the bucket — docs/adr/0039.

    It mirrors what the bucket reports (`size`, `content_type`, `etag`,
    `last_modified`) so that a listing renders without a `stat` per row, and it
    holds what the bucket cannot answer: who uploaded it, when this catalogue
    first saw it, what a person wrote about it, and the digest of the bytes.

    `sha256` is `None` on a file this API never received — one the pipeline or
    the MinIO console wrote, picked up later by a reconcile. Recording a digest
    there would mean downloading the object to compute it, and a catch-up that
    reads every byte of a bucket is not a catch-up.
    """

    id: UUID
    key: str
    size: int
    content_type: str
    last_modified: datetime
    created_at: datetime
    updated_at: datetime
    etag: str = ""
    sha256: str | None = None
    details: FileDetails = field(default_factory=FileDetails)
    #: The Keycloak subject and username of whoever uploaded it, empty for a
    #: file this API never received.
    uploaded_by_subject: str = ""
    uploaded_by: str = ""


@dataclass(frozen=True, slots=True)
class CatalogedFile:
    """One object of the bucket, with the row describing it when there is one.

    `metadata` is `None` for a file written straight into the bucket and not yet
    reconciled. That is a listing with a blank description, never a missing
    file: the bytes are what exist, the row is what is known about them.
    """

    stored: StoredFile
    metadata: FileMetadata | None = None

    @property
    def key(self) -> str:
        return self.stored.key

    @property
    def name(self) -> str:
        return self.stored.name

    @property
    def size(self) -> int:
        return self.stored.size


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """What one pass of the catch-up changed: rows added, rows dropped.

    Two counts and not a list: the answer an operator needs from a reconcile is
    whether anything had drifted, and a bucket of ten thousand files would make
    a list of names the wrong shape of answer.
    """

    recorded: int
    forgotten: int


@dataclass(frozen=True, slots=True)
class CatalogedListing:
    """One folder, each file paired with what the catalogue knows about it."""

    prefix: str
    folders: tuple[str, ...]
    files: tuple[CatalogedFile, ...]
    truncated: bool


def _refuse_ambiguous(path: str) -> None:
    if len(path.encode()) > MAX_KEY_BYTES:
        msg = f"a file path is at most {MAX_KEY_BYTES} bytes"
        raise InvalidFileKeyError(msg)
    if any(unicodedata.category(character) == "Cc" for character in path):
        msg = "a file path cannot hold a control character"
        raise InvalidFileKeyError(msg)
    if path.startswith("/"):
        msg = "a file path cannot start with '/'"
        raise InvalidFileKeyError(msg)
    if any(segment in {"", ".", ".."} for segment in path.split("/")):
        msg = f"a file path cannot hold an empty, '.' or '..' segment: {path!r}"
        raise InvalidFileKeyError(msg)


def clean_key(key: str) -> str:
    """The key of one file, refused when it could name another one."""
    if not key:
        msg = "a file path cannot be empty"
        raise InvalidFileKeyError(msg)
    _refuse_ambiguous(key)
    return key


def clean_prefix(prefix: str) -> str:
    """A folder: empty for the top of the bucket, otherwise ending in `/`."""
    if not prefix:
        return ""
    folder = prefix if prefix.endswith("/") else f"{prefix}/"
    _refuse_ambiguous(folder[:-1])
    return folder


def join_key(prefix: str, name: str) -> str:
    """The key of a file called `name` inside the folder `prefix`."""
    return clean_key(clean_prefix(prefix) + name)


def guess_content_type(key: str) -> str:
    guessed, _ = mimetypes.guess_type(key, strict=False)
    return guessed or DEFAULT_CONTENT_TYPE


def sha256_of(raw: bytes) -> str:
    """The digest recorded beside a file this API received, as hex.

    Content addressing, not integrity against an attacker: it answers "is this
    the same file under another name?", which a bucket cannot.
    """
    return hashlib.sha256(raw).hexdigest()


def _clean_tag(tag: str) -> str:
    cleaned = tag.strip().lower()
    if len(cleaned) > MAX_TAG_LENGTH:
        msg = f"a tag is at most {MAX_TAG_LENGTH} characters: {tag!r}"
        raise InvalidFileMetadataError(msg)
    if any(unicodedata.category(character) == "Cc" for character in cleaned):
        msg = f"a tag cannot hold a control character: {tag!r}"
        raise InvalidFileMetadataError(msg)
    return cleaned


def clean_details(
    *,
    title: str = "",
    description: str = "",
    tags: Sequence[str] = (),
) -> FileDetails:
    """What is stored when a person describes a file, or a refusal.

    Tags are lowercased and de-duplicated in the order they were given, so that
    `Réseau` and `réseau` are one tag rather than two spellings nobody can
    filter on together. Order is kept because a person wrote it.
    """
    kept_title = title.strip()
    if len(kept_title) > MAX_FILE_TITLE_LENGTH:
        msg = f"a title is at most {MAX_FILE_TITLE_LENGTH} characters"
        raise InvalidFileMetadataError(msg)
    kept_description = description.strip()
    if len(kept_description) > MAX_FILE_DESCRIPTION_LENGTH:
        msg = f"a description is at most {MAX_FILE_DESCRIPTION_LENGTH} characters"
        raise InvalidFileMetadataError(msg)
    if len(tags) > MAX_FILE_TAGS:
        msg = f"a file carries at most {MAX_FILE_TAGS} tags"
        raise InvalidFileMetadataError(msg)
    kept_tags: dict[str, None] = {}
    for tag in tags:
        cleaned = _clean_tag(tag)
        if cleaned:
            kept_tags[cleaned] = None
    return FileDetails(title=kept_title, description=kept_description, tags=tuple(kept_tags))
