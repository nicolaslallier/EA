"""The relationship matrix: elements by elements, one cell per ordered pair.

Pure by construction. The matrix states no rule of its own — what may link two
elements is asked of `domain/archimate/rules.py` — and it stores nothing: a
cell is a reading of the `relationships` table, and creating a link from a
cell goes through `POST /relationships` like any other (docs/adr/0009).

A link lives in exactly one cell, (row = source, column = target). Its reverse
is another cell. That is Sparx EA's reading too, and it keeps a cell's meaning
one sentence long: "what runs from this row to this column".
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from ea.domain.archimate import (
    ElementType,
    Layer,
    RelationshipType,
    permits,
    permitted_relationships,
)
from ea.domain.model import Element, Relationship

#: How many elements one axis shows. Declared here and imported by
#: `api/schemas.py` — a service cannot import the API (CLAUDE.md).
MAX_MATRIX_AXIS: Final = 50

Allowed = Callable[[Element, Element], tuple[RelationshipType, ...]]


def allowed_between(source: Element, target: Element) -> tuple[RelationshipType, ...]:
    """The relationships ArchiMate lets run from `source` to `target`.

    `permitted_relationships` answers for two *types*; whether the two ends are
    one element is what makes `A composes A` illegal while `component composes
    component` stays legal, so that is passed to `permits` here.
    """
    same = source.id == target.id
    return tuple(
        relationship
        for relationship in permitted_relationships(source.element_type, target.element_type)
        if permits(relationship, source.element_type, target.element_type, same_element=same)
    )


@dataclass(frozen=True, slots=True)
class AxisSelection:
    """What one axis is made of: named elements, or the catalogue narrowed by criteria.

    Nothing set means the whole catalogue (cut at `MAX_MATRIX_AXIS`).
    """

    element_ids: tuple[UUID, ...] = ()
    element_types: tuple[ElementType, ...] = ()
    layers: tuple[Layer, ...] = ()
    search: str | None = None
    #: A package (sub-packages included) or a saved query; at most one, and never with the rest.
    package_id: UUID | None = None
    query_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class MatrixAxis:
    elements: tuple[Element, ...]
    total: int

    @classmethod
    def of(cls, elements: Sequence[Element], total: int | None = None) -> MatrixAxis:
        kept = tuple(elements[:MAX_MATRIX_AXIS])
        claimed = len(elements) if total is None else total
        return cls(elements=kept, total=max(claimed, len(kept)))

    @property
    def truncated(self) -> bool:
        return self.total > len(self.elements)


@dataclass(frozen=True, slots=True)
class MatrixCell:
    source_id: UUID
    target_id: UUID
    links: tuple[Relationship, ...]
    allowed: tuple[RelationshipType, ...]


@dataclass(frozen=True, slots=True)
class Matrix:
    rows: MatrixAxis
    cols: MatrixAxis
    #: Only the cells holding a link: the grid is sparse by nature.
    cells: tuple[MatrixCell, ...]
    #: Pairs with no link where the metamodel permits nothing.
    blocked: tuple[tuple[UUID, UUID], ...]


def build_matrix(
    rows: MatrixAxis,
    cols: MatrixAxis,
    relationships: Iterable[Relationship],
    allowed: Allowed = allowed_between,
) -> Matrix:
    """Place each relationship in its cell and mark the pairs nothing can link."""
    by_pair: dict[tuple[UUID, UUID], list[Relationship]] = {}
    for link in relationships:
        by_pair.setdefault((link.source_id, link.target_id), []).append(link)

    cells: list[MatrixCell] = []
    blocked: list[tuple[UUID, UUID]] = []
    for row in rows.elements:
        for col in cols.elements:
            links = tuple(
                sorted(
                    by_pair.get((row.id, col.id), ()), key=lambda link: (link.created_at, link.id)
                )
            )
            permitted = allowed(row, col)
            if links:
                cells.append(MatrixCell(row.id, col.id, links, permitted))
            elif not permitted:
                blocked.append((row.id, col.id))
    return Matrix(rows=rows, cols=cols, cells=tuple(cells), blocked=tuple(blocked))
