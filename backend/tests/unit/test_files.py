"""Which paths a file may be stored under, and what a stored file says about itself."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from ea.domain.errors import InvalidFileKeyError, InvalidFileMetadataError
from ea.domain.files import (
    DEFAULT_CONTENT_TYPE,
    MAX_FILE_DESCRIPTION_LENGTH,
    MAX_FILE_TAGS,
    MAX_FILE_TITLE_LENGTH,
    MAX_KEY_BYTES,
    MAX_TAG_LENGTH,
    CatalogedFile,
    FileDetails,
    StoredFile,
    clean_details,
    clean_key,
    clean_prefix,
    guess_content_type,
    join_key,
    sha256_of,
)


class TestAKey:
    @pytest.mark.parametrize(
        "key", ["notes.md", "inbox/notes.md", "a/b/c/rapport 2026.pdf", "é/ü.txt"]
    )
    def test_an_ordinary_path_is_kept_as_it_is(self, key: str) -> None:
        assert clean_key(key) == key

    @pytest.mark.parametrize(
        "key",
        [
            "",
            "/notes.md",
            "inbox/",
            "inbox//notes.md",
            "./notes.md",
            "inbox/../secret",
            "..",
            "a\x00b",
            "a\nb",
        ],
    )
    def test_a_path_that_could_mean_something_else_is_refused(self, key: str) -> None:
        with pytest.raises(InvalidFileKeyError):
            clean_key(key)

    def test_the_length_is_counted_in_bytes_like_s3_does(self) -> None:
        clean_key("é" * (MAX_KEY_BYTES // 2))
        with pytest.raises(InvalidFileKeyError):
            clean_key("é" * (MAX_KEY_BYTES // 2 + 1))


class TestAPrefix:
    def test_empty_is_the_top_of_the_bucket(self) -> None:
        assert clean_prefix("") == ""

    @pytest.mark.parametrize(
        ("given", "expected"), [("inbox", "inbox/"), ("inbox/", "inbox/"), ("a/b", "a/b/")]
    )
    def test_a_folder_always_ends_with_a_slash(self, given: str, expected: str) -> None:
        assert clean_prefix(given) == expected

    @pytest.mark.parametrize("prefix", ["/", "/inbox", "a//b", "../", "inbox/./"])
    def test_a_folder_that_could_escape_is_refused(self, prefix: str) -> None:
        with pytest.raises(InvalidFileKeyError):
            clean_prefix(prefix)

    def test_a_name_is_joined_under_its_folder(self) -> None:
        assert join_key("inbox", "notes.md") == "inbox/notes.md"
        assert join_key("", "notes.md") == "notes.md"

    def test_a_name_that_is_not_a_name_is_refused_once_joined(self) -> None:
        with pytest.raises(InvalidFileKeyError):
            join_key("inbox/", "")


def test_a_file_is_named_by_the_last_segment_of_its_key() -> None:
    stored = StoredFile(
        key="inbox/sub/notes.md",
        size=3,
        last_modified=datetime(2026, 9, 15, tzinfo=UTC),
        content_type="text/markdown",
    )
    assert stored.name == "notes.md"


@pytest.mark.parametrize(
    ("key", "expected"),
    [("a.pdf", "application/pdf"), ("a.png", "image/png"), ("a.md", "text/markdown")],
)
def test_the_content_type_is_guessed_from_the_extension(key: str, expected: str) -> None:
    assert guess_content_type(key) == expected


def test_an_unknown_extension_is_plain_bytes() -> None:
    assert guess_content_type("dump.zzz-unknown") == DEFAULT_CONTENT_TYPE


class TestWhatAPersonSaysAboutAFile:
    def test_nothing_said_is_the_empty_description(self) -> None:
        assert clean_details() == FileDetails()

    def test_a_title_and_a_description_are_trimmed(self) -> None:
        details = clean_details(title="  Rapport 2026 ", description=" Le bilan.\n")
        assert (details.title, details.description) == ("Rapport 2026", "Le bilan.")

    def test_tags_are_lowercased_trimmed_and_deduplicated_in_order(self) -> None:
        details = clean_details(tags=("Réseau", " budget ", "réseau", ""))
        assert details.tags == ("réseau", "budget")

    def test_a_title_longer_than_the_bound_is_refused(self) -> None:
        with pytest.raises(InvalidFileMetadataError):
            clean_details(title="a" * (MAX_FILE_TITLE_LENGTH + 1))

    def test_a_description_longer_than_the_bound_is_refused(self) -> None:
        with pytest.raises(InvalidFileMetadataError):
            clean_details(description="a" * (MAX_FILE_DESCRIPTION_LENGTH + 1))

    def test_more_tags_than_the_bound_are_refused(self) -> None:
        with pytest.raises(InvalidFileMetadataError):
            clean_details(tags=tuple(f"t{n}" for n in range(MAX_FILE_TAGS + 1)))

    def test_a_tag_longer_than_the_bound_is_refused(self) -> None:
        with pytest.raises(InvalidFileMetadataError):
            clean_details(tags=("a" * (MAX_TAG_LENGTH + 1),))

    @pytest.mark.parametrize("tag", ["a\nb", "a\x00b"])
    def test_a_tag_holding_a_control_character_is_refused(self, tag: str) -> None:
        with pytest.raises(InvalidFileMetadataError):
            clean_details(tags=(tag,))


def test_the_digest_is_the_sha256_of_the_bytes() -> None:
    assert sha256_of(b"# A") == hashlib.sha256(b"# A").hexdigest()


def test_a_listing_pairs_each_file_with_the_row_that_describes_it() -> None:
    stored = StoredFile(
        key="inbox/a.md",
        size=3,
        last_modified=datetime(2026, 9, 15, tzinfo=UTC),
        content_type="text/markdown",
    )
    cataloged = CatalogedFile(stored=stored, metadata=None)

    assert (cataloged.key, cataloged.name, cataloged.size) == ("inbox/a.md", "a.md", 3)
