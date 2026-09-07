"""What a markdown document is, and what is refused at the door.

Every rule here is about the choice made in docs/adr/0017: the markdown is
stored as *text*. A file that is not text cannot be accepted and then dealt
with later — `text` has no room for it — so the refusals live in the domain,
where every adapter gets the same answer and the same sentence.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.domain.documents import (
    MAX_DOCUMENT_BYTES,
    Document,
    clean_filename,
    decode_markdown,
)
from tests.conftest import FIXED_NOW

ELEMENT = uuid4()


def a_document(**overrides: object) -> Document:
    fields: dict[str, object] = {
        "element_id": ELEMENT,
        "filename": "runbook.md",
        "content": "# Runbook\n",
        "now": FIXED_NOW,
    }
    fields.update(overrides)
    return Document.create(**fields)  # type: ignore[arg-type]


class TestDecoding:
    def test_a_utf8_file_becomes_the_text_that_will_be_stored(self) -> None:
        assert decode_markdown("# Café\n".encode()) == "# Café\n"

    def test_a_byte_order_mark_is_not_kept_as_a_character(self) -> None:
        """A markdown file saved by a Windows editor starts with one."""
        assert decode_markdown(b"\xef\xbb\xbf# Titre\n") == "# Titre\n"

    def test_a_file_that_is_not_utf8_is_refused_rather_than_mangled(self) -> None:
        with pytest.raises(ValueError, match="UTF-8"):
            decode_markdown(b"\xff\xfe\x00binary")

    def test_a_file_holding_a_nul_is_refused(self) -> None:
        """Regression: `text` cannot hold a NUL, so PostgreSQL would reject it.

        Latin-1 bytes happen to decode as UTF-8 here, so without this check a
        renamed binary would only fail at `INSERT` — as a driver error, in a
        log, long after the upload it belongs to.
        """
        with pytest.raises(ValueError, match="NUL"):
            decode_markdown(b"# Titre\n\x00\n")

    def test_a_file_past_the_size_limit_is_refused(self) -> None:
        with pytest.raises(ValueError, match="larger than"):
            decode_markdown(b"#" * (MAX_DOCUMENT_BYTES + 1))

    def test_a_file_exactly_at_the_limit_is_accepted(self) -> None:
        assert len(decode_markdown(b"#" * MAX_DOCUMENT_BYTES)) == MAX_DOCUMENT_BYTES


class TestFileNames:
    @pytest.mark.parametrize("name", ["notes.md", "NOTES.MD", "contrat.markdown"])
    def test_a_markdown_name_is_kept_as_it_was_uploaded(self, name: str) -> None:
        assert clean_filename(f"  {name} ") == name

    @pytest.mark.parametrize("name", ["notes.txt", "notes", "notes.md.exe"])
    def test_anything_that_is_not_markdown_is_refused(self, name: str) -> None:
        with pytest.raises(ValueError, match="markdown"):
            clean_filename(name)

    @pytest.mark.parametrize("name", ["../../etc/passwd.md", "dir/notes.md", "a\\b.md"])
    def test_a_name_carrying_a_path_is_refused(self, name: str) -> None:
        """The name is attacker-controlled, stored, listed and shown back."""
        with pytest.raises(ValueError, match="path"):
            clean_filename(name)

    def test_a_blank_name_is_refused(self) -> None:
        with pytest.raises(ValueError, match="file name"):
            clean_filename("   ")

    def test_an_absurdly_long_name_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at most"):
            clean_filename(f"{'a' * 400}.md")


class TestDocument:
    def test_a_new_document_carries_its_element_and_both_timestamps(self) -> None:
        document = a_document()

        assert document.element_id == ELEMENT
        assert document.created_at == document.updated_at == FIXED_NOW

    def test_the_size_is_derived_from_the_text_so_it_cannot_drift(self) -> None:
        """Counted in bytes, not characters: 'é' is one character and two bytes."""
        assert a_document(content="é").byte_size == 2

    def test_an_empty_file_is_a_failed_upload_and_not_a_document(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            a_document(content="   \n")

    def test_revising_keeps_the_identity_and_moves_only_the_update_time(self) -> None:
        original = a_document()
        later = FIXED_NOW.replace(year=2027)

        revised = original.revise("# Runbook v2\n", now=later)

        assert (revised.id, revised.filename) == (original.id, original.filename)
        assert revised.created_at == FIXED_NOW
        assert revised.updated_at == later
        assert revised.content == "# Runbook v2\n"

    def test_a_revision_is_held_to_the_same_rules_as_an_upload(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            a_document().revise("", now=FIXED_NOW)

    def test_content_reaching_the_entity_without_bytes_is_still_bounded(self) -> None:
        """`Document.create` is reachable from an adapter that decoded nothing.

        `decode_markdown` guards the upload path; these two guard the entity
        itself, so a caller that already has text cannot store what a file
        could not have carried.
        """
        with pytest.raises(ValueError, match="larger than"):
            a_document(content="#" * (MAX_DOCUMENT_BYTES + 1))

        with pytest.raises(ValueError, match="NUL"):
            a_document(content="# Titre\n\x00")

    def test_a_summary_says_everything_but_the_text(self) -> None:
        document = a_document(content="# Titre\n")

        summary = document.summary

        assert (summary.id, summary.filename) == (document.id, "runbook.md")
        assert summary.byte_size == 8
        assert not hasattr(summary, "content")
