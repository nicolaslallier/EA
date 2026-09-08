"""Cutting a markdown document into the passages a semantic index can hold.

A whole document is the wrong unit for retrieval twice over: a megabyte of
prose averages into one vector that resembles nothing in particular, and an
answer that points at `runbook.md` still leaves the reader to find the
paragraph. So a document is cut where its author already cut it — at its
headings.

**The trail of headings above a passage is carried with it, and is embedded in
front of it.** That is the whole idea of docs/adr/0019: "Restart the pod" means
nothing on its own, and everything under
`runbook.md > Incidents > Escalation`. The section knows what it is about only
because of the headings it sits beneath, and those words are absent from its
own text — so they are put back before the passage is embedded, and the file
name is the root of the trail, since it is the one heading every document has.

Nothing here imports a framework, a driver or an embedding client: this decides
what a passage *is*, and it is unit-tested by comparing strings.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

#: The longest passage that is embedded whole. It is in characters rather than
#: tokens so that nothing here needs a tokenizer — a dependency that would have
#: to match whichever model is configured, and would be wrong the day it
#: changed. Roughly 400 tokens of prose: small enough that a hit points at a
#: paragraph rather than a chapter, large enough to hold an argument.
MAX_CHUNK_CHARS: Final = 1800

#: Carried from the end of one window into the start of the next, so a sentence
#: split across the boundary is still whole in one of the two.
CHUNK_OVERLAP_CHARS: Final = 200

#: What one heading may contribute to a trail before it is elided. A heading
#: this long is a paragraph someone typed after a `#`.
MAX_HEADING_CHARS: Final = 120

#: A megabyte of prose is about 550 windows; the bound is what stops a
#: pathological file — one blank-line-separated word per line — from becoming
#: an unbounded embedding bill.
MAX_CHUNKS_PER_DOCUMENT: Final = 800

#: What a window may hold once the overlap carried into it is counted, so that
#: overlap plus one piece never exceeds `MAX_CHUNK_CHARS`.
_BUDGET: Final = MAX_CHUNK_CHARS - CHUNK_OVERLAP_CHARS - 2

#: CommonMark's ATX heading: up to three spaces of indent, one to six hashes,
#: then *whitespace* before the title. The required space is what keeps `#tag`
#: — which people write in prose, and in French rather often — from opening a
#: section. Four spaces of indent is an indented code block, which is why the
#: leading run is bounded rather than `\s*`.
_ATX: Final = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")

#: A fenced code block, opened and closed by at least three backticks or
#: tildes. Fences matter here for one reason: `# comment` is the first line of
#: half the shell examples ever written, and reading it as a heading would cut
#: a runbook in the middle of the command it is telling you to run.
_FENCE: Final = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")

#: `## Titre ##` — the optional closing run is decoration, not part of the title.
_CLOSING_HASHES: Final = re.compile(r"[ \t]+#+[ \t]*$")

#: The delimiter of YAML front matter, and also a thematic break, which is why
#: it is only honoured on the very first line and only when it closes again.
_FRONT_MATTER: Final = re.compile(r"^(---|\.\.\.)[ \t]*$")

_BLANK_LINE: Final = re.compile(r"\n[ \t]*\n")


@dataclass(frozen=True, slots=True)
class Chunk:
    """One passage of a document, with the headings it sits beneath.

    `heading_path` is the trail *above* the passage and never includes the file
    name: the file name belongs to the document, and joining the two is
    `heading_trail`'s job so that a renamed document does not mean re-cutting
    one.
    """

    ordinal: int
    heading_path: tuple[str, ...]
    text: str


def split_markdown(content: str) -> tuple[Chunk, ...]:
    """Cut a markdown document into passages, in reading order.

    A section — a heading and the text under it, down to the next heading of
    any level — is one passage, unless it is longer than a window, in which
    case it becomes several that overlap. A heading whose body is empty yields
    no passage of its own; it is not lost, because it is part of the trail of
    every section beneath it.
    """
    lines = content.splitlines()
    body_starts_at = _after_front_matter(lines)
    sections = _sections(lines[body_starts_at:])

    chunks: list[Chunk] = []
    for heading_path, section in sections:
        for window in _windows(section):
            if len(chunks) >= MAX_CHUNKS_PER_DOCUMENT:
                return tuple(chunks)
            chunks.append(Chunk(ordinal=len(chunks), heading_path=heading_path, text=window))
    if chunks:
        return tuple(chunks)

    # Every section was empty — a document of headings alone, or a table of
    # contents. It is still a document, and one passage of it is better than a
    # document nobody can find.
    whole = "\n".join(lines[body_starts_at:]).strip()
    return (Chunk(ordinal=0, heading_path=(), text=whole),) if whole else ()


def heading_trail(filename: str, heading_path: Sequence[str]) -> str:
    """Where a passage sits, said in one line: `runbook.md > Incidents > Escalation`.

    The file name is the root because it is the only heading every document is
    guaranteed to have, and because it is what a reader is shown afterwards —
    the string that explains a hit is the string that was embedded.

    It takes the path rather than a `Chunk` so that a `Passage` read back out
    of the index renders its trail the same way the indexer rendered it: one
    function, so the two cannot drift into two spellings of the same line.
    """
    parts = [filename, *(heading for heading in heading_path if heading)]
    return " > ".join(_shortened(part) for part in parts)


def embedding_input(filename: str, chunk: Chunk) -> str:
    """The text actually handed to the embedding model for a passage.

    Not the passage: the passage *plus* the trail above it. A section that says
    "restart the pod and wait" carries none of the words that say what it is
    about; the trail does, and putting it back is the difference between a
    corpus that answers questions and one that matches keywords.
    """
    return f"{heading_trail(filename, chunk.heading_path)}\n\n{chunk.text}"


def _after_front_matter(lines: list[str]) -> int:
    """Where the prose starts, skipping a YAML front-matter block if there is one.

    Only when the very first line opens it *and* a later line closes it: three
    dashes are also a thematic break, and a file that starts with one is
    content, not metadata. The block itself is deliberately not indexed — a
    search hit on `owner: nicolas` is a hit on a form field, not on prose.
    """
    if not lines or lines[0].strip() != "---":
        return 0
    for index, line in enumerate(lines[1:], start=1):
        if _FRONT_MATTER.match(line):
            return index + 1
    return 0


def _sections(lines: list[str]) -> list[tuple[tuple[str, ...], str]]:
    """The document as (trail, text) pairs, one per heading plus the preamble.

    Empty sections are dropped here rather than by the caller, so that a
    container heading costs nothing and a passage is never blank.
    """
    collected: list[tuple[tuple[str, ...], list[str]]] = []
    path: list[str] = []
    body: list[str] = []
    collected.append(((), body))
    fence: tuple[str, int] | None = None

    for line in lines:
        fence, inside_code = _fence_state(fence, line)
        heading = None if inside_code else _atx_heading(line)
        if heading is None:
            body.append(line)
            continue
        level, title = heading
        # A level-2 heading after a level-3 one pops back to its own depth; a
        # level skipped altogether collapses rather than leaving a hole.
        del path[level - 1 :]
        path.append(title)
        body = []
        collected.append((tuple(path), body))

    return [(trail, text) for trail, lines_of in collected if (text := "\n".join(lines_of).strip())]


def _fence_state(fence: tuple[str, int] | None, line: str) -> tuple[tuple[str, int] | None, bool]:
    """Track whether this line is inside a fenced code block.

    A fence closes only on the character it opened with, at least as long, and
    carrying no info string — so a ``` inside a ~~~ block is text, which is
    exactly how a markdown file documenting markdown is written.
    """
    match = _FENCE.match(line)
    if fence is None:
        if match is None:
            return None, False
        return (match.group(1)[0], len(match.group(1))), True
    character, length = fence
    if (
        match is not None
        and match.group(1)[0] == character
        and len(match.group(1)) >= length
        and not match.group(2).strip()
    ):
        return None, True
    return fence, True


def _atx_heading(line: str) -> tuple[int, str] | None:
    match = _ATX.match(line)
    if match is None:
        return None
    return len(match.group(1)), _CLOSING_HASHES.sub("", match.group(2) or "").strip()


def _windows(text: str) -> list[str]:
    """One section as the passages it fits into, overlapping where it is split."""
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    windows: list[str] = []
    current = ""
    for block in _paragraphs(text):
        for piece in _hard_split(block):
            if current and len(current) + 2 + len(piece) > MAX_CHUNK_CHARS:
                windows.append(current)
                current = _overlap(current)
            current = f"{current}\n\n{piece}" if current else piece
    if current.strip():
        windows.append(current)
    return windows


def _paragraphs(text: str) -> list[str]:
    """Blocks separated by a blank line — the boundary an author already chose."""
    return [block.strip() for block in _BLANK_LINE.split(text) if block.strip()]


def _hard_split(block: str) -> list[str]:
    """A paragraph too long for a window, cut on lines and then on characters.

    Cutting mid-word is ugly and is still better than dropping the text: a
    generated table or a base64 blob has no line breaks to cut on, and it is
    not the common case anything here is tuned for.
    """
    if len(block) <= _BUDGET:
        return [block]
    pieces: list[str] = []
    current = ""
    for line in block.splitlines():
        while len(line) > _BUDGET:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:_BUDGET])
            line = line[_BUDGET:]
        if current and len(current) + 1 + len(line) > _BUDGET:
            pieces.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current.strip():
        pieces.append(current)
    return pieces


def _overlap(window: str) -> str:
    """The tail of a window, carried into the next one.

    Cut at a line break inside the tail rather than at the character bound, so
    the next window starts on something readable.
    """
    tail = window[-CHUNK_OVERLAP_CHARS:]
    _, newline, after = tail.partition("\n")
    return (after if newline else tail).strip()


def _shortened(heading: str) -> str:
    if len(heading) <= MAX_HEADING_CHARS:
        return heading
    return heading[: MAX_HEADING_CHARS - 1].rstrip() + "…"
