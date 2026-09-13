# Diagram builder — design

Date: 2026-09-13. Status: approved (approach A).

## What the user asked for

A screen to build diagrams: every element on the left, drag an element onto the
diagram, draw links between elements, and see the full detail of the element
selected.

## Decisions

1. **A diagram is an ArchiMate *view*.** It owns no facts: elements and
   relationships stay in Neo4j. A diagram only records *which* elements are
   shown and *where* (x, y).
2. **Diagrams are saved** — named, listed, reopened, deleted — in PostgreSQL
   ("everything not a graph", `docs/adr/0015`). Recorded as `docs/adr/0031`.
3. **A drawn link is a real relationship**: `POST /relationships`, offering only
   the types `/metamodel/relationships` permits for the pair. Relationships that
   already exist between elements on a diagram are drawn automatically.
4. **No graph library.** Hand-written SVG, as `GraphDiagram.vue` (ADR 0013);
   geometry in a pure, unit-tested module `frontend/src/lib/diagramGeometry.ts`.
5. **Removing a box from a diagram never deletes the element.**
6. **Autosave**: the whole layout is PUT, debounced (`lib/debounce.ts`), after
   each drop, move-end or removal. No Save button.

## Storage — migration `0004`

```
diagrams(id uuid pk, name varchar(200) not null, description text not null default '',
         created_at timestamptz, updated_at timestamptz)
         unique(name)
diagram_nodes(diagram_id uuid fk -> diagrams.id ON DELETE CASCADE,
              element_id uuid not null (index; NO FK — the element is a Neo4j node),
              x double precision not null, y double precision not null,
              pk(diagram_id, element_id))
```

Mapped in `backend/src/ea/db/models/diagram.py`, imported by
`db/models/__init__.py`. Naming convention of `db/base.py` is frozen — use it.

When an element is deleted, `ArchitectureService.delete_element` must also drop
its `diagram_nodes` rows, through the existing `ElementAttachments` port
(`discard_for_element`) — do not invent a second cascade mechanism. As a second
line of defence, reading a diagram skips any node whose element no longer
exists.

## HTTP API (router `backend/src/ea/api/diagrams.py`, bounds in `api/schemas.py`)

| Method | Path | Body | Answer |
|---|---|---|---|
| GET | `/diagrams` | — | `200 list[DiagramSummaryRead]`, sorted by name |
| POST | `/diagrams` | `DiagramCreate` | `201 DiagramSummaryRead`; `409` duplicate name |
| GET | `/diagrams/{diagram_id}` | — | `200 DiagramRead`; `404` |
| PATCH | `/diagrams/{diagram_id}` | `DiagramUpdate` | `200 DiagramSummaryRead`; `404`, `409` |
| DELETE | `/diagrams/{diagram_id}` | — | `204`; `404` |
| PUT | `/diagrams/{diagram_id}/layout` | `DiagramLayout` | `204`; `404` unknown diagram; `422` unknown element / duplicate element id |

Schemas (inputs `extra="forbid"`):

- `DiagramCreate { name: str (1..200, stripped), description: str = "" }`
- `DiagramUpdate { name?: str, description?: str }`
- `DiagramNode { element_id: UUID, x: float, y: float }` — x, y bounded to
  `[-100_000, 100_000]`
- `DiagramLayout { nodes: list[DiagramNode] }` — at most `MAX_DIAGRAM_NODES = 500`
- `DiagramSummaryRead { id, name, description, node_count, created_at, updated_at }`
- `DiagramRead { id, name, description, created_at, updated_at,
  nodes: list[DiagramNode], elements: list[ElementRead],
  relationships: list[RelationshipRead] }` — `relationships` are those whose
  **both** ends are on the diagram (one new Cypher query on the graph
  repository: both endpoints' ids in `$ids`, bound parameter).

`PUT .../layout` replaces the whole node set in one transaction.

Errors reuse the typed envelopes of `api/errors.py` and the domain errors of
`domain/errors.py`. `DiagramService` (`services/diagrams.py`) owns transactions
and logs one line per write (`action=diagram_created`, …), per `docs/adr/0021`.

Not in this change: MCP tools for diagrams, paging of `/diagrams`.

## SPA — section `/diagrammes`

Entry in `frontend/src/router/sections.ts`: path `/diagrammes`, name
`diagrams`, label `Diagrammes`, group `model`, lazy view
`features/diagrams/DiagramsSection.vue`.

- **No `?diagram=`**: list of diagrams (open, create with a name, delete with a
  confirmation).
- **`?diagram=<id>`**: the editor, three columns.
  - **Palette (left)**: `GET /elements?search=` (debounced search), grouped by
    layer with `LAYER_LABELS`/`LAYER_COLOURS`; each row `draggable`, the element
    id in `dataTransfer`. Elements already on the diagram are dimmed, not
    draggable.
  - **Canvas (centre)**: SVG. Drop places the box (coordinates through
    `diagramGeometry`), pointer-drag moves it (pointer capture), click selects.
    A handle on the selected box drags a rubber-band line; releasing on another
    box opens a small popover listing permitted relationship types (labels from
    `features/relationships/labels.ts`; `access_type` only for `access`,
    `directed` only for `association`, as `RelationshipPanel.vue` does). Edges
    are straight lines clipped to box borders, labelled with the type. Zoom
    buttons as in `GraphDiagram`. Keyboard: a selected box can be removed with
    `Delete`; boxes are focusable.
  - **Detail (right)**: `ElementDetail.vue` via `useElementDetail`, plus a
    *Retirer du diagramme* button. Selection is `?element=<id>` (replace, not
    push).
- Every load goes through `useLatestRequest()`; failures land in composable
  state, write refusals are shown by the screen.
- Types come only from the generated client (`src/api/schema.d.ts`); no
  hand-written payload interface.

## Geometry module — `frontend/src/lib/diagramGeometry.ts`

Pure, no DOM, no Vue. Reuses `BOX_WIDTH`/`BOX_HEIGHT` from `lib/graphLayout.ts`.

- `toCanvasPoint(client: Point, origin: Point, zoom: number): Point` — a pointer
  position in client pixels to canvas units (origin = the SVG's client
  top-left, already including scroll).
- `centreOf(position: Point): Point` — a box's stored `(x, y)` is its top-left.
- `placeCentredAt(point: Point): Point` — the top-left for a box dropped centred
  on `point`, clamped so `x, y >= 0`.
- `clipToBox(from: Point, boxTopLeft: Point): Point` — where the segment from
  `from` to the box's centre crosses the box border.
- `edgeSegment(source: Point, target: Point): { from: Point; to: Point } | null`
  — both ends clipped; `null` when the boxes overlap (nothing sensible to draw).
- `canvasExtent(positions: Point[], margin: number): { width: number; height: number }`
  — at least a minimum size, growing to contain every box plus margin.

## Testing (TDD)

- Backend unit: `DiagramService` with in-memory doubles; schema bounds.
- Backend integration (throwaway containers): SQL store (cascade, replace
  layout, unique name), new Cypher query.
- Backend API (`tests/e2e`): every status code above.
- Frontend Vitest: `diagramGeometry`, the diagram composable, the canvas
  component (drop, move, select, link popover), the section.
- `make openapi` regenerates the client; `make check` green.
