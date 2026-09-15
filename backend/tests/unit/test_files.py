"""Which paths a file may be stored under, and what a stored file says about itself."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ea.domain.errors import InvalidFileKeyError
from ea.domain.files import (
    DEFAULT_CONTENT_TYPE,
    MAX_KEY_BYTES,
    StoredFile,
    clean_key,
    clean_prefix,
    guess_content_type,
    join_key,
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
