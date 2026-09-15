"""What a stored file is, and which paths a file may be stored under.

Pure: the bucket itself is `repositories/object_store.py`'s business. A folder
is not a stored thing in S3 — it is the shared start of some keys — so a
listing *computes* folders from keys and nothing here ever writes one. See
docs/adr/0036.
"""

from __future__ import annotations

import mimetypes
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from ea.domain.errors import InvalidFileKeyError

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

# Register markdown MIME type for the domain (later tasks rely on it)
mimetypes.add_type("text/markdown", ".md")
mimetypes.add_type("text/markdown", ".markdown")


@dataclass(frozen=True, slots=True)
class StoredFile:
    key: str
    size: int
    last_modified: datetime
    content_type: str

    @property
    def name(self) -> str:
        return self.key.rsplit("/", 1)[-1]


@dataclass(frozen=True, slots=True)
class FileListing:
    prefix: str
    folders: tuple[str, ...]
    files: tuple[StoredFile, ...]
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
