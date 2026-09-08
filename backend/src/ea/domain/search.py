"""What a semantic index of the documents holds, and what it answers with.

The document store already answers "which files hang off this element". This
answers a different question — "where is it written that…" — and it answers it
with a *passage*, not a file: a hit that names `runbook.md` still leaves the
reader to find the paragraph, and an agent that has to read a megabyte to check
one sentence has not been helped.

Three things are decisions rather than details, and all three are here because
they are contract and not implementation — see docs/adr/0019.

**The width of a vector is fixed.** `EMBEDDING_DIMENSIONS` is the width of the
stored column, so it is the one number the settings, the migration and the
model have to agree on. They agree by importing it rather than by three people
typing 1024.

**A vector remembers which model produced it.** Cosine distance between two
vectors from two different models is a number with no meaning, so a corpus half
re-embedded is not a slightly worse corpus, it is a broken one. Every stored
passage carries its model and every search filters on it: a change of model
therefore returns *nothing* until the reindex is done, which is loud, rather
than nonsense, which is not.

**Passages are asymmetric.** Several embedding families want a different prefix
on a stored passage and on a query, so the port has two methods rather than
one. A model that wants no prefix simply implements both the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from ea.domain.chunking import heading_trail

#: The width of `document_chunks.embedding`, and therefore of every vector this
#: application stores or compares. Changing it is a migration and a full
#: reindex, never a setting someone edits — which is why the settings validate
#: against this constant instead of declaring their own.
#:
#: 1024 is `bge-m3`'s, the multilingual model docs/adr/0019 deploys. A model of
#: another width can be configured, and boot refuses it in as many words.
EMBEDDING_DIMENSIONS: Final = 1024

#: How many passages a search returns unless it is asked for fewer or more.
DEFAULT_SEARCH_LIMIT: Final = 5
MAX_SEARCH_LIMIT: Final = 50


@dataclass(frozen=True, slots=True)
class EmbeddedChunk:
    """One passage of a document, ready to be stored: its text and its vector.

    It carries the model rather than leaving it to the repository, because the
    vector and the name of what produced it are one fact. A row that holds the
    first without the second is a row nobody can safely compare to anything.
    """

    ordinal: int
    heading_path: tuple[str, ...]
    text: str
    embedding: tuple[float, ...]
    model: str


@dataclass(frozen=True, slots=True)
class Passage:
    """One hit: a passage of one document, and how close it came.

    The element is named by id and not by name. Names live in the graph, and
    reading the graph to decorate a relational query would make every search a
    call to Neo4j for a field the caller may not want — `get_element` is one
    call away when it does.
    """

    document_id: UUID
    element_id: UUID
    filename: str
    heading_path: tuple[str, ...]
    text: str
    score: float

    @property
    def trail(self) -> str:
        """Where the passage sits, rendered exactly as it was embedded."""
        return heading_trail(self.filename, self.heading_path)
