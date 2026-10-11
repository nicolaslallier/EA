"""The model analysis endpoints: the relationship matrix, the quality checks, the dashboards.

Everything here reads. `POST /matrix` is a POST only because two axes do not
fit a query string; creating or removing a link from a cell goes through
`POST /relationships` and `DELETE /relationships/{id}`, so no rule of the
metamodel is bypassed by a second write path (docs/adr/0056).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from ea.api.dependencies import Analysis
from ea.api.schemas import (
    ErrorResponse,
    MatrixCellDetailRead,
    MatrixQuery,
    MatrixRead,
)

router = APIRouter(tags=["analysis"])

NOT_FOUND: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}
}
REFUSED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
}


# --- The relationship matrix ----------------------------------------------


@router.post("/matrix", response_model=MatrixRead, responses=REFUSED)
async def read_matrix(payload: MatrixQuery, service: Analysis) -> MatrixRead:
    """The grid of two axes, with the links between them.

    Row = source, column = target: a link lives in exactly one cell. An axis
    given as criteria is cut at the first 50 by name and `truncated` says so;
    an axis given as more than 50 element ids is refused.
    """
    return MatrixRead.of(await service.matrix(payload.rows.selection(), payload.cols.selection()))


@router.get("/matrix/cell", response_model=MatrixCellDetailRead, responses=NOT_FOUND)
async def read_matrix_cell(
    service: Analysis,
    source: Annotated[UUID, Query(description="The element of the row.")],
    target: Annotated[UUID, Query(description="The element of the column.")],
) -> MatrixCellDetailRead:
    """One pair: the links that exist and the relationships still permitted."""
    return MatrixCellDetailRead.of(await service.cell(source, target))
