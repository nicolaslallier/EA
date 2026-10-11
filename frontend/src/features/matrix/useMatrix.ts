// The matrix screen's state: one grid, one open cell, and the two writes.
//
// The grid and the cell are two independent questions, so each has its own
// `useLatestRequest` (lib/latest.ts): opening a cell must not cancel the grid
// being reloaded beside it. Writes are the *existing* endpoints — a link is
// created by `POST /relationships` and removed by `DELETE /relationships/{id}`
// — so the metamodel stays the only judge of what a cell accepts.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import { axisToBody, type AxisChoice } from './axis'

export type MatrixRead = components['schemas']['MatrixRead']
export type MatrixCellDetailRead = components['schemas']['MatrixCellDetailRead']
export type RelationshipType = components['schemas']['RelationshipType']

export function useMatrix() {
  const matrix = ref<MatrixRead | null>(null)
  const detail = ref<MatrixCellDetailRead | null>(null)
  const grid = useLatestRequest()
  const cell = useLatestRequest()
  let lastAxes: { rows: AxisChoice; cols: AxisChoice } | null = null

  async function load(rows: AxisChoice, cols: AxisChoice): Promise<void> {
    lastAxes = { rows, cols }
    await grid.run(
      async (signal) =>
        unwrap(
          await api.POST('/matrix', {
            body: { rows: axisToBody(rows), cols: axisToBody(cols) },
            signal,
          }),
        ),
      (answer) => {
        matrix.value = answer
      },
      () => {
        // A refused question must not leave the previous answer on screen.
        matrix.value = null
      },
    )
  }

  async function openCell(source: string, target: string): Promise<void> {
    await cell.run(
      async (signal) =>
        unwrap(await api.GET('/matrix/cell', { params: { query: { source, target } }, signal })),
      (answer) => {
        detail.value = answer
      },
      () => {
        detail.value = null
      },
    )
  }

  function closeCell(): void {
    cell.cancel()
    detail.value = null
  }

  async function refresh(): Promise<void> {
    const open = detail.value
    await Promise.all([
      open ? openCell(open.source_id, open.target_id) : Promise.resolve(),
      lastAxes ? load(lastAxes.rows, lastAxes.cols) : Promise.resolve(),
    ])
  }

  async function connect(type: RelationshipType): Promise<void> {
    const open = detail.value
    if (!open) {
      return
    }
    unwrap(
      await api.POST('/relationships', {
        body: { relationship_type: type, source_id: open.source_id, target_id: open.target_id },
      }),
    )
    await refresh()
  }

  async function disconnect(relationshipId: string): Promise<void> {
    unwrap(
      await api.DELETE('/relationships/{relationship_id}', {
        params: { path: { relationship_id: relationshipId } },
      }),
    )
    await refresh()
  }

  return {
    matrix,
    detail,
    status: grid.status,
    error: grid.error,
    cellStatus: cell.status,
    cellError: cell.error,
    load,
    openCell,
    closeCell,
    connect,
    disconnect,
  }
}
