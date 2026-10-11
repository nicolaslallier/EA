import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'
import { createMemoryHistory, type Router } from 'vue-router'

import MatrixSection from '../src/features/matrix/MatrixSection.vue'
import { useMe } from '../src/lib/me'
import { createAppRouter } from '../src/router'
import { aCellDetail, aGrid, aRelationship, METAMODEL, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const SOURCE = 'aaaaaaaa-1111-4111-8111-111111111111'
const TARGET = 'bbbbbbbb-2222-4222-8222-222222222222'

const PALETTE: Route = { path: '/metamodel', body: METAMODEL }
const GRID: Route = { method: 'POST', path: '/matrix', body: aGrid() }
const CELL: Route = { path: '/matrix/cell', body: aCellDetail() }
const CREATE: Route = { method: 'POST', path: '/relationships', status: 201, body: aRelationship() }

async function open(routes: Route[], query = ''): Promise<{ router: Router; calls: ReturnType<typeof stubApi> }> {
  const calls = stubApi([PALETTE, GRID, ...routes])
  const router = createAppRouter(createMemoryHistory())
  // A first entry to go back to: the memory history has none of its own.
  await router.push('/')
  await router.push(`/analyse/matrice${query}`)
  await router.isReady()
  render(MatrixSection, { global: { plugins: [router] } })
  return { router, calls }
}

function asReader(): void {
  vi.mocked(useMe).mockReturnValueOnce({
    me: ref({ username: 'reader', can_write: false, can_approve: false }),
    canWrite: computed(() => false),
    error: ref(null),
    load: vi.fn(() => Promise.resolve()),
  })
}

describe('MatrixSection', () => {
  it('draws the grid the API returns', async () => {
    await open([])

    expect(await screen.findByRole('grid', { name: /matrice de relations/i })).toBeInTheDocument()
    expect(screen.getByRole('gridcell', { name: /Invoice API vers Order to cash/ })).toBeInTheDocument()
  })

  it('reads the axes from the URL and sends them as the API wants', async () => {
    const { calls } = await open([], '?rows=layer:business&cols=type:node')

    await screen.findByRole('grid')
    const post = calls.find((call) => call.method === 'POST' && call.url.pathname === '/matrix')
    expect(post?.body).toEqual({ rows: { layers: ['business'] }, cols: { element_types: ['node'] } })
  })

  it('warns when an axis was cut, with the total', async () => {
    stubApi([PALETTE, { method: 'POST', path: '/matrix', body: aGrid({ truncated: true, rows_total: 130 }) }])
    const router = createAppRouter(createMemoryHistory())
    await router.push('/analyse/matrice')
    await router.isReady()
    render(MatrixSection, { global: { plugins: [router] } })

    expect(await screen.findByText(/130/)).toBeInTheDocument()
    expect(screen.getByText(/affinez/i)).toBeInTheDocument()
  })

  it('opens a cell by replacing the URL, and offers only what the API permits', async () => {
    const { router } = await open([CELL])
    const cell = await screen.findByRole('gridcell', { name: /Invoice API vers Order to cash/ })

    await fireEvent.click(cell)

    await waitFor(() => expect(router.currentRoute.value.query.cell).toBe(`${SOURCE},${TARGET}`))
    const select = await screen.findByLabelText(/nouveau lien/i)
    const options = within(select).getAllByRole('option').map((option) => option.textContent?.trim())
    expect(options).toEqual(['Choisis une relation…', 'sert', 'est associé à'])
    // Replaced, not pushed: going back leaves the screen instead of closing the cell.
    router.back()
    await waitFor(() => expect(router.currentRoute.value.path).not.toBe('/analyse/matrice'))
  })

  it('pushes a history entry when an axis changes', async () => {
    const { router } = await open([], '?rows=all')
    await screen.findByRole('grid')

    await fireEvent.update(screen.getByLabelText(/lignes/i), 'layer:business')
    await waitFor(() => expect(router.currentRoute.value.query.rows).toBe('layer:business'))
    router.back()

    await waitFor(() => expect(router.currentRoute.value.query.rows).toBe('all'))
  })

  it('creates a link from the open cell through POST /relationships', async () => {
    const { calls } = await open([CELL, CREATE], `?cell=${SOURCE},${TARGET}`)
    const select = await screen.findByLabelText(/nouveau lien/i)

    await fireEvent.update(select, 'association')
    await fireEvent.click(screen.getByRole('button', { name: /^relier$/i }))

    await waitFor(() => {
      const post = calls.find((call) => call.url.pathname === '/relationships')
      expect(post?.body).toEqual({
        relationship_type: 'association',
        source_id: SOURCE,
        target_id: TARGET,
      })
    })
  })

  it('removes a link after a confirmation', async () => {
    const link = aRelationship()
    const { calls } = await open(
      [CELL, { method: 'DELETE', path: `/relationships/${link.id}`, status: 204 }],
      `?cell=${SOURCE},${TARGET}`,
    )

    await fireEvent.click(await screen.findByRole('button', { name: /^supprimer$/i }))
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    await fireEvent.click(screen.getByRole('button', { name: /^confirmer$/i }))

    await waitFor(() => expect(calls.some((call) => call.method === 'DELETE')).toBe(true))
  })

  it('offers a reader the list of links and no write control', async () => {
    asReader()
    await open([CELL], `?cell=${SOURCE},${TARGET}`)

    expect(await screen.findByText(/sert/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /supprimer/i })).toBeNull()
    expect(screen.queryByLabelText(/nouveau lien/i)).toBeNull()
  })

  it('shows the API refusal in words', async () => {
    await open(
      [
        CELL,
        {
          method: 'POST',
          path: '/relationships',
          status: 422,
          body: {
            error: 'illegal_relationship',
            detail: 'serving: data_object -> business_process is not permitted',
          },
        },
      ],
      `?cell=${SOURCE},${TARGET}`,
    )

    await fireEvent.update(await screen.findByLabelText(/nouveau lien/i), 'serving')
    await fireEvent.click(screen.getByRole('button', { name: /^relier$/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/not permitted/)
  })
})
