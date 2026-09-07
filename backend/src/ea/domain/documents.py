"""Markdown attached to an architecture element.

An element carries a `documentation` field already, and this is not a second
one. That field is a paragraph typed into a form; this is a *file* a modeller
already wrote — a runbook, an interface contract, a decision note — kept whole,
under its own name, so it can be uploaded again from the repository it lives in.

It is stored as **text**, never as bytes: markdown is prose, it is searched,
diffed and read by people, and a `bytea` column would make every one of those
a decoding step. That choice is what the rules below enforce — a file that is
not UTF-8, or that holds a NUL, is refused at the door rather than corrupting a
column later.

Nothing here imports a framework or a driver, per `CLAUDE.md`: the same rules
hold whichever adapter uploaded the file.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID, uuid4

#: Markdown that no longer fits here is not markdown any more — it is an export.
#: The bound is on the *bytes* rather than the characters, because that is what
#: the column, the request body and the memory of the process all count.
MAX_DOCUMENT_BYTES: Final = 1_000_000

#: The two suffixes markdown is actually saved under. The check is on the name
#: and not on the `Content-Type`: browsers report markdown as `text/markdown`,
#: `text/x-markdown` or `application/octet-stream` depending on the platform,
#: so trusting that header would reject files at random.
MARKDOWN_SUFFIXES: Final = (".md", ".markdown")

MAX_FILENAME_LENGTH: Final = 200

#: An uploaded file name is attacker-controlled and is stored, listed and shown
#: back. It is never joined onto a path here — nothing writes to disk — but a
#: name carrying a separator would still be a trap for whatever does next.
_ILLEGAL_IN_A_FILENAME: Final = ("/", "\\", "\x00")


def decode_markdown(raw: bytes) -> str:
    """Turn an uploaded file into the text that will be stored, or refuse it.

    `utf-8-sig` rather than `utf-8`: a markdown file saved by a Windows editor
    starts with a byte-order mark, which would otherwise become a stray
    character at the top of every stored document.

    The NUL check is not theoretical. PostgreSQL cannot hold one in a `text`
    column, so a binary file renamed `.md` that happened to decode would fail
    at `INSERT` — as a driver error, in a log, long after the upload.
    """
    if len(raw) > MAX_DOCUMENT_BYTES:
        msg = f"the file is larger than {MAX_DOCUMENT_BYTES // 1000} kB"
        raise ValueError(msg)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        msg = "the file is not UTF-8 text — a markdown document is stored as text"
        raise ValueError(msg) from error
    if "\x00" in text:
        msg = "the file holds a NUL byte, so it is not text"
        raise ValueError(msg)
    return text


def clean_filename(filename: str) -> str:
    """The name a document is stored and listed under.

    A document is identified to a human by its file name, so the name has to be
    one: not blank, not a path, and ending in a markdown suffix.
    """
    cleaned = filename.strip()
    if not cleaned:
        msg = "a document needs a file name"
        raise ValueError(msg)
    if cleaned in {".", ".."} or any(char in cleaned for char in _ILLEGAL_IN_A_FILENAME):
        msg = f"{filename!r} is a path, not a file name"
        raise ValueError(msg)
    if len(cleaned) > MAX_FILENAME_LENGTH:
        msg = f"a file name is at most {MAX_FILENAME_LENGTH} characters"
        raise ValueError(msg)
    if not cleaned.lower().endswith(MARKDOWN_SUFFIXES):
        allowed = " or ".join(MARKDOWN_SUFFIXES)
        msg = f"{cleaned!r} is not a markdown file — its name must end in {allowed}"
        raise ValueError(msg)
    return cleaned


@dataclass(frozen=True, slots=True)
class DocumentSummary:
    """A document without its content — one row of the list attached to an element.

    A separate type rather than a `Document` with a nullable `content`: listing
    ten documents of a megabyte each to display their names is the mistake this
    exists to make impossible, and a `content` that is sometimes absent would
    hide it behind an optional field.
    """

    id: UUID
    element_id: UUID
    filename: str
    byte_size: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Document:
    """One markdown file, attached to exactly one element.

    Frozen like every other entity here: a revision is a new object, so nothing
    can mutate a document another layer is still holding.
    """

    id: UUID
    element_id: UUID
    filename: str
    content: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        element_id: UUID,
        filename: str,
        content: str,
        now: datetime,
        document_id: UUID | None = None,
    ) -> Document:
        return cls(
            id=document_id or uuid4(),
            element_id=element_id,
            filename=clean_filename(filename),
            content=_valid_content(content),
            created_at=now,
            updated_at=now,
        )

    def revise(self, content: str, *, now: datetime) -> Document:
        """The same document, holding a new version of the same file."""
        return Document(
            id=self.id,
            element_id=self.element_id,
            filename=self.filename,
            content=_valid_content(content),
            created_at=self.created_at,
            updated_at=now,
        )

    @property
    def byte_size(self) -> int:
        """What the file weighs — derived, so it can never drift from the text."""
        return len(self.content.encode("utf-8"))

    @property
    def summary(self) -> DocumentSummary:
        return DocumentSummary(
            id=self.id,
            element_id=self.element_id,
            filename=self.filename,
            byte_size=self.byte_size,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


def _valid_content(content: str) -> str:
    """An empty file is a failed upload, not a document.

    The size is checked here as well as in `decode_markdown`, because content
    reaches this from adapters that never held the bytes.
    """
    if not content.strip():
        msg = "the file is empty"
        raise ValueError(msg)
    if len(content.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        msg = f"the file is larger than {MAX_DOCUMENT_BYTES // 1000} kB"
        raise ValueError(msg)
    if "\x00" in content:
        msg = "the file holds a NUL byte, so it is not text"
        raise ValueError(msg)
    return content
