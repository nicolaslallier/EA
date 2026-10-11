import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import MatrixGrid from '../src/features/matrix/MatrixGrid.vue'
import { aGrid, aRelationship } from './support/api'

const API = {
  id: 'aaaaaaaa-1111-4111-8111-111111111111',
  name: 'Invoice API',
  element_type: 'application_service',
  layer: 'application',
} as const
const PROCESS = {
  id: 'bbbbbbbb-2222-4222-8222-222222222222',
  name: 'Order to cash',
  element_type: 'business_process',
  layer: 'business',
} as const

/** Both elements on both axes, one link running API → PROCESS only. */
const SQUARE = aGrid({
  rows: [API, PROCESS],
  cols: [API, PROCESS],
  rows_total: 2,
  cols_total: 2,
  cells: [{ source_id: API.id, target_id: PROCESS.id, links: [aRelationship()] }],
})

function renderGrid(matrix = SQUARE, selected = null) {
  return render(MatrixGrid, { props: { matrix, selected } })
}

describe('MatrixGrid', () => {
  it('is a grid with the names as headers', () => {
    renderGrid()

    expect(screen.getByRole('grid', { name: /matrice de relations/i })).toBeInTheDocument()
    expect(screen.getAllByRole('columnheader', { name: 'Order to cash' })).toHaveLength(1)
    expect(screen.getAllByRole('rowheader', { name: 'Invoice API' })).toHaveLength(1)
  })

  it('draws a link in its own direction only', () => {
    renderGrid()

    const forward = screen.getByRole('gridcell', { name: /Invoice API vers Order to cash/ })
    const backward = screen.getByRole('gridcell', { name: /Order to cash vers Invoice API/ })

    expect(within(forward).getByText(/sert/)).toBeInTheDocument()
    expect(within(backward).queryByText(/sert/)).toBeNull()
  })

  it('keeps exactly one cell in the tab order', () => {
    renderGrid()

    const reachable = screen.getAllByRole('gridcell').filter((cell) => cell.tabIndex === 0)
    expect(reachable).toHaveLength(1)
  })

  it('moves the focus with the arrows', async () => {
    renderGrid()
    const cells = screen.getAllByRole('gridcell')
    cells[0].focus()

    await fireEvent.keyDown(cells[0], { key: 'ArrowRight' })

    await waitFor(() => expect(document.activeElement).toBe(cells[1]))
    expect(cells[1].tabIndex).toBe(0)
    expect(cells[0].tabIndex).toBe(-1)
  })

  it('opens the cell on Enter and closes on Escape', async () => {
    const view = renderGrid()
    const cell = screen.getByRole('gridcell', { name: /Invoice API vers Order to cash/ })

    await fireEvent.keyDown(cell, { key: 'Enter' })
    await fireEvent.keyDown(cell, { key: 'Escape' })

    expect(view.emitted().open).toEqual([[API.id, PROCESS.id]])
    expect(view.emitted().close).toHaveLength(1)
  })

  it('does not open a pair the metamodel permits nothing for', async () => {
    const view = renderGrid(aGrid({ ...SQUARE, blocked: [{ source_id: PROCESS.id, target_id: API.id }] }))
    const cell = screen.getByRole('gridcell', { name: /Order to cash vers Invoice API/ })

    await fireEvent.keyDown(cell, { key: 'Enter' })

    expect(cell).toHaveAttribute('aria-disabled', 'true')
    expect(view.emitted().open).toBeUndefined()
  })

  it('says so, and draws no grid, when an axis is empty', () => {
    renderGrid(aGrid({ rows: [], rows_total: 0, cells: [] }))

    expect(screen.getByText(/aucun élément/i)).toBeInTheDocument()
    expect(screen.queryByRole('grid')).toBeNull()
  })
})
