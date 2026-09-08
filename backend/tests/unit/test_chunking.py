"""Cutting a markdown document into the passages the index holds.

This is the half of the retrieval that decides what can ever be found, and it
is pure: no embedding, no database, no clock. A section is a chunk, the trail
of headings above it is carried with it, and an oversized section is windowed
rather than truncated — see docs/adr/0019.
"""

from __future__ import annotations

import pytest

from ea.domain.chunking import (
    CHUNK_OVERLAP_CHARS,
    MAX_CHUNK_CHARS,
    MAX_CHUNKS_PER_DOCUMENT,
    Chunk,
    embedding_input,
    heading_trail,
    split_markdown,
)


def paths(content: str) -> list[tuple[str, ...]]:
    return [chunk.heading_path for chunk in split_markdown(content)]


class TestSplittingAtHeadings:
    def test_each_section_becomes_its_own_passage(self) -> None:
        chunks = split_markdown("# Runbook\n\nQuoi faire.\n\n## Escalade\n\nAppeler Nicolas.\n")

        assert [chunk.heading_path for chunk in chunks] == [
            ("Runbook",),
            ("Runbook", "Escalade"),
        ]
        assert chunks[1].text == "Appeler Nicolas."

    def test_the_heading_trail_deepens_and_pops_back(self) -> None:
        """A level-2 heading after a level-3 one drops the level-3 from the path."""
        content = "# A\n\na\n\n## B\n\nb\n\n### C\n\nc\n\n## D\n\nd\n"

        assert paths(content) == [("A",), ("A", "B"), ("A", "B", "C"), ("A", "D")]

    def test_a_skipped_level_collapses_rather_than_leaving_a_hole(self) -> None:
        assert paths("# A\n\na\n\n### C\n\nc\n") == [("A",), ("A", "C")]

    def test_text_before_the_first_heading_is_a_passage_with_no_trail(self) -> None:
        chunks = split_markdown("Une note sans titre.\n\n# Titre\n\nsuite\n")

        assert chunks[0].heading_path == ()
        assert chunks[0].text == "Une note sans titre."

    def test_a_heading_with_no_body_of_its_own_yields_no_passage(self) -> None:
        """Its title is not lost — it is the trail of every section beneath it."""
        assert paths("# Conteneur\n\n## Contenu\n\ndu texte\n") == [("Conteneur", "Contenu")]

    def test_ordinals_number_the_passages_in_reading_order(self) -> None:
        chunks = split_markdown("# A\n\na\n\n# B\n\nb\n\n# C\n\nc\n")

        assert [chunk.ordinal for chunk in chunks] == [0, 1, 2]

    def test_closing_hashes_are_not_part_of_the_title(self) -> None:
        assert paths("## Titre ##\n\ncorps\n") == [("Titre",)]

    def test_a_hash_with_no_space_after_it_is_not_a_heading(self) -> None:
        """CommonMark requires the space; `#tag` is prose, and a tag at that."""
        chunks = split_markdown("#pasuntitre\n\ncorps\n")

        assert chunks[0].heading_path == ()
        assert "#pasuntitre" in chunks[0].text


class TestThingsThatLookLikeHeadingsAndAreNot:
    def test_a_comment_inside_a_fenced_block_does_not_open_a_section(self) -> None:
        content = "# Runbook\n\n```bash\n# redemarrer le service\nsystemctl restart ea\n```\n"

        assert paths(content) == [("Runbook",)]

    def test_a_tilde_fence_closes_only_on_tildes(self) -> None:
        content = "# A\n\n~~~\n# pas un titre\n```\n# non plus\n~~~\n\n## B\n\nb\n"

        assert paths(content) == [("A",), ("A", "B")]

    def test_an_indented_code_block_holds_no_headings_either(self) -> None:
        assert paths("# A\n\n    # indente, donc du code\n\ntexte\n") == [("A",)]

    def test_front_matter_is_metadata_and_is_not_indexed(self) -> None:
        content = "---\ntitle: Runbook\nowner: nicolas\n---\n\n# Runbook\n\ncorps\n"
        chunks = split_markdown(content)

        assert [chunk.heading_path for chunk in chunks] == [("Runbook",)]
        assert "owner: nicolas" not in chunks[0].text

    def test_an_unterminated_front_matter_fence_is_left_alone(self) -> None:
        """Three dashes at the top of a file that never closes them is content."""
        chunks = split_markdown("---\n\nune note\n")

        assert "une note" in chunks[0].text


class TestOversizedSections:
    def test_a_long_section_is_windowed_rather_than_truncated(self) -> None:
        paragraph = "Une phrase de remplissage. " * 40  # ~1080 characters
        content = "# Grand\n\n" + "\n\n".join([paragraph] * 4)

        chunks = split_markdown(content)

        assert len(chunks) > 1
        assert all(len(chunk.text) <= MAX_CHUNK_CHARS for chunk in chunks)

    def test_every_window_keeps_the_trail_of_the_section_it_came_from(self) -> None:
        content = "# Grand\n\n## Section\n\n" + ("phrase. " * 30 + "\n\n") * 12

        assert {chunk.heading_path for chunk in split_markdown(content)} == {("Grand", "Section")}

    def test_consecutive_windows_overlap_so_a_sentence_is_never_cut_in_half(self) -> None:
        content = "# Grand\n\n" + ("bloc unique de texte. " * 25 + "\n\n") * 8
        chunks = split_markdown(content)

        assert len(chunks) > 1
        tail = chunks[0].text[-CHUNK_OVERLAP_CHARS:]
        assert any(line and line in chunks[1].text for line in tail.splitlines())

    def test_one_paragraph_longer_than_a_window_is_split_all_the_same(self) -> None:
        chunks = split_markdown("# Grand\n\n" + "x" * (MAX_CHUNK_CHARS * 3))

        assert len(chunks) > 1
        assert all(len(chunk.text) <= MAX_CHUNK_CHARS for chunk in chunks)

    def test_the_number_of_passages_per_document_is_bounded(self) -> None:
        """A megabyte of prose must not become an unbounded embedding bill."""
        chunks = split_markdown("# A\n\n" + ("x" * 40 + "\n\n") * 5000)

        assert len(chunks) <= MAX_CHUNKS_PER_DOCUMENT


class TestTheFallback:
    def test_a_document_of_headings_alone_still_yields_one_passage(self) -> None:
        chunks = split_markdown("# A\n\n## B\n\n### C\n")

        assert len(chunks) == 1
        assert "# A" in chunks[0].text


class TestWhatIsActuallyEmbedded:
    def test_the_trail_starts_at_the_file_name(self) -> None:
        """The file name is the one heading every document has — see docs/adr/0019."""
        assert (
            heading_trail("runbook.md", ("Runbook", "Escalade"))
            == "runbook.md > Runbook > Escalade"
        )

    def test_a_passage_with_no_heading_still_names_its_file(self) -> None:
        assert heading_trail("notes.md", ()) == "notes.md"

    def test_the_embedded_text_is_the_trail_then_the_passage(self) -> None:
        chunk = Chunk(ordinal=0, heading_path=("Escalade",), text="Appeler Nicolas.")

        assert embedding_input("runbook.md", chunk) == "runbook.md > Escalade\n\nAppeler Nicolas."

    def test_an_absurdly_long_heading_is_shortened_in_the_trail(self) -> None:
        assert len(heading_trail("a.md", ("T" * 500,))) < 300


@pytest.mark.parametrize("content", ["", "   \n\n  \n"])
def test_a_blank_document_yields_no_passages(content: str) -> None:
    """`Document` refuses one before this is reached; it must not crash here."""
    assert split_markdown(content) == ()
