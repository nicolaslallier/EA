"""Analysing the model: the matrix, the quality checks, the dashboards.

Nothing here writes and nothing here is stored — an analysis is computed on
demand from `elements` and `relationships`, because a stored result would be a
second truth that every edit made stale (docs/adr/0056). Writing a link
from a matrix cell is `ArchitectureService.connect`, called by the caller.

Every public method asks who is calling first (`require_caller`): all of them
are reads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from ea.domain.errors import ElementNotFoundError, MatrixAxisTooLargeError
from ea.domain.matrix import (
    MAX_MATRIX_AXIS,
    AxisSelection,
    Matrix,
    MatrixAxis,
    MatrixCell,
    allowed_between,
    build_matrix,
)
from ea.domain.ports import ElementFilter
from ea.services.caller import require_caller

if TYPE_CHECKING:
    from ea.domain.ports import AnalysisRepository
    from ea.services.architecture import ArchitectureService


class AnalysisService:
    """The analysis use cases, over the architecture service and three extra reads."""

    def __init__(
        self,
        architecture: ArchitectureService,
        repository: AnalysisRepository,
    ) -> None:
        self._architecture = architecture
        self._repository = repository

    # --- The relationship matrix -------------------------------------------

    async def matrix(self, rows: AxisSelection, cols: AxisSelection) -> Matrix:
        """The grid of two axes with the links between them."""
        require_caller()
        row_axis = await self._axis(rows)
        col_axis = await self._axis(cols)
        links = await self._repository.links_between(
            [element.id for element in row_axis.elements],
            [element.id for element in col_axis.elements],
        )
        return build_matrix(row_axis, col_axis, links)

    async def cell(self, source_id: UUID, target_id: UUID) -> MatrixCell:
        """One ordered pair: its links, and what ArchiMate would still let us add."""
        require_caller()
        source = await self._architecture.get_element(source_id)
        target = await self._architecture.get_element(target_id)
        links = await self._repository.links_between([source_id], [target_id])
        return MatrixCell(
            source_id=source_id,
            target_id=target_id,
            links=tuple(sorted(links, key=lambda link: (link.created_at, link.id))),
            allowed=allowed_between(source, target),
        )

    async def _axis(self, selection: AxisSelection) -> MatrixAxis:
        if selection.element_ids:
            ids = tuple(dict.fromkeys(selection.element_ids))
            if len(ids) > MAX_MATRIX_AXIS:
                msg = f"an axis names {len(ids)} elements; the grid shows at most {MAX_MATRIX_AXIS}"
                raise MatrixAxisTooLargeError(msg)
            found = await self._repository.elements_by_ids(ids)
            missing = sorted(str(i) for i in set(ids) - {element.id for element in found})
            if missing:
                msg = f"no element with id {missing[0]}"
                raise ElementNotFoundError(msg)
            return MatrixAxis.of(found)
        criteria = ElementFilter(
            element_types=selection.element_types,
            layers=selection.layers,
            search=selection.search,
            limit=MAX_MATRIX_AXIS + 1,
        )
        elements = await self._architecture.list_elements(criteria)
        total = (
            len(elements)
            if len(elements) <= MAX_MATRIX_AXIS
            else await self._architecture.count_elements(criteria)
        )
        return MatrixAxis.of(elements, total)
