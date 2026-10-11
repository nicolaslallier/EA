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
    AggregateRead,
    Cursor,
    DimensionParam,
    ErrorResponse,
    FindingsLimit,
    HeatmapRead,
    MatrixCellDetailRead,
    MatrixQuery,
    MatrixRead,
    PropertyKey,
    QualityReportRead,
)
from ea.domain.analytics import Dimension, Metric
from ea.domain.exchange import Severity
from ea.domain.quality import RuleCode

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


FindingsQuery = Annotated[FindingsLimit, Query()]
CursorQuery = Annotated[Cursor, Query()]


# --- Quality checks -----------------------------------------------------------


@router.get(
    "/quality",
    response_model=QualityReportRead,
    responses={status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse}},
)
async def read_quality(
    service: Analysis,
    severity: Annotated[Severity | None, Query()] = None,
    code: Annotated[RuleCode | None, Query()] = None,
    limit: FindingsQuery = 100,
    cursor: CursorQuery = 0,
) -> QualityReportRead:
    """Run the quality rules over the catalogue and page through the findings.

    Computed on every call — nothing is stored. The counts describe the whole
    run; `severity` and `code` narrow the findings only.
    """
    return QualityReportRead.of(
        await service.check(severity=severity, code=code, limit=limit, cursor=cursor)
    )


PropertyQuery = Annotated[PropertyKey | None, Query(alias="property")]
INVALID: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse}
}


# --- Aggregates and heat maps ---------------------------------------------------


@router.get("/analytics/aggregate", response_model=AggregateRead, responses=INVALID)
async def read_aggregate(
    service: Analysis,
    by: Annotated[DimensionParam, Query()],
    metric: Annotated[Metric, Query()] = Metric.COUNT,
    property_key: PropertyQuery = None,
) -> AggregateRead:
    """Elements grouped by one dimension, each bucket with a count, a metric and a level.

    `sum`, `avg`, `min` and `max` read `property` as a number (`.` or `,`
    decimal, spaces as thousands separators); a value that does not read is
    counted in `unreadable`, never silently ignored. `level` (0 to 4) is the
    server's colour decision.
    """
    dimension = Dimension.parse(by)
    return AggregateRead.of(await service.aggregate(dimension, metric, property_key))


@router.get("/analytics/heatmap", response_model=HeatmapRead, responses=INVALID)
async def read_heatmap(
    service: Analysis,
    rows: Annotated[DimensionParam, Query()],
    cols: Annotated[DimensionParam, Query()],
    metric: Annotated[Metric, Query()] = Metric.COUNT,
    property_key: PropertyQuery = None,
) -> HeatmapRead:
    """The same, grouped by two dimensions: a grid of cells with a level each."""
    result = await service.heatmap(
        Dimension.parse(rows), Dimension.parse(cols), metric, property_key
    )
    return HeatmapRead.of(result)
