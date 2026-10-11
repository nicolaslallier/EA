import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import DashboardSection from '../src/features/dashboard/DashboardSection.vue'
import { catalogueQuery, keyLabel, swatchOf } from '../src/features/dashboard/presentation'
import { createAppRouter } from '../src/router'
import { aHeatmap, anAggregate, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const TILES: Route = { path: '/analytics/aggregate', body: anAggregate() }
const HEAT: Route = { path: '/analytics/heatmap', body: aHeatmap() }

async function open(routes: Route[] = [TILES, HEAT], query = '') {
  const calls = stubApi(routes)
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/analyse/tableau-de-bord${query}`)
  await router.isReady()
  const view = render(DashboardSection, { global: { plugins: [router] } })
  return { calls, router, ...view }
}

describe('the presentation tables', () => {
  it('maps a level to a swatch with its own ink, and no level to a neutral one', () => {
    expect(swatchOf(0).fill).not.toBe(swatchOf(4).fill)
    expect(swatchOf(4).ink).not.toBe(swatchOf(0).ink)
    expect(swatchOf(null).fill).toBeTruthy()
  })

  it('names the sentinel buckets and translates a layer', () => {
    expect(keyLabel('__unset__')).toBe('Non renseigné')
    expect(keyLabel('__invalid__')).toBe('Valeur illisible')
    expect(keyLabel('business')).toBe('Métier')
    expect(keyLabel('en production')).toBe('en production')
  })

  it('links a cell to the catalogue by the dimension the catalogue can filter on', () => {
    expect(catalogueQuery('layer', 'business', 'property:lifecycle', 'retiré')).toEqual({ layer: 'business' })
    expect(catalogueQuery('property:risk', 'élevé', 'element_type', 'node')).toEqual({ type: 'node' })
    expect(catalogueQuery('property:risk', 'élevé', 'property:lifecycle', 'retiré')).toBeNull()
    expect(catalogueQuery('layer', '__unset__', 'property:lifecycle', 'retiré')).toBeNull()
  })
})

describe('DashboardSection', () => {
  it('shows a tile per bucket with the total', async () => {
    await open()

    const tiles = within(await screen.findByRole('list', { name: /répartition/i }))
    expect(tiles.getByText('Métier')).toBeInTheDocument()
    expect(tiles.getByText('Technologie')).toBeInTheDocument()
    expect(tiles.getByText(/3 éléments/)).toBeInTheDocument()
  })

  it('draws the heat map as rectangles with the value printed in the cell', async () => {
    const { container } = await open()
    await screen.findByRole('img', { name: /carte de chaleur/i })

    expect(container.querySelectorAll('svg rect').length).toBeGreaterThanOrEqual(6)
    const printed = within(screen.getByRole('img', { name: /carte de chaleur/i })).getAllByText('2')
    expect(printed.length).toBeGreaterThan(0)
  })

  it('titles each cell for the tooltip with its row, column and count', async () => {
    await open()

    expect(await screen.findByText(/Technologie.*en production.*2/)).toBeInTheDocument()
  })

  it('links a cell to the catalogue filtered on its layer', async () => {
    await open()

    const link = (await screen.findByText(/Technologie.*en production/)).closest('a')
    expect(link).toHaveAttribute('href', '/elements?layer=technology')
  })

  it('labels the sentinel columns and draws a legend of the levels received', async () => {
    await open()

    expect(await screen.findByText('Non renseigné')).toBeInTheDocument()
    const legend = screen.getByRole('list', { name: /légende/i })
    expect(within(legend).getAllByRole('listitem')).toHaveLength(3) // levels 0 and 4, and "no data"
  })

  it('warns when values were unreadable, and says where to look', async () => {
    await open([TILES, { path: '/analytics/heatmap', body: aHeatmap({ unreadable: 2 }) }])

    const warning = await screen.findByRole('status')
    expect(warning).toHaveTextContent(/2 valeurs illisibles/)
    expect(within(warning).getByRole('link')).toHaveAttribute('href', expect.stringContaining('code=unreadable_property'))
  })

  it('sends the question from the URL', async () => {
    const { calls } = await open(
      [TILES, HEAT],
      '?by=element_type&cols=property:risk&metric=sum&property=cost',
    )

    await screen.findByRole('img', { name: /carte de chaleur/i })
    const heat = calls.find((call) => call.url.pathname === '/analytics/heatmap')
    expect(heat?.url.searchParams.get('rows')).toBe('element_type')
    expect(heat?.url.searchParams.get('cols')).toBe('property:risk')
    expect(heat?.url.searchParams.get('property')).toBe('cost')
  })

  it('writes a changed dimension into the URL', async () => {
    const { router } = await open()
    await screen.findByRole('img', { name: /carte de chaleur/i })

    await fireEvent.update(screen.getByLabelText(/lignes/i), 'element_type')

    await waitFor(() => expect(router.currentRoute.value.query.by).toBe('element_type'))
  })

  it('says the model is empty rather than drawing an empty grid', async () => {
    await open([
      { path: '/analytics/aggregate', body: anAggregate({ total: 0, buckets: [] }) },
      { path: '/analytics/heatmap', body: aHeatmap({ rows: [], cols: [], cells: [], total: 0 }) },
    ])

    expect(await screen.findByText(/aucun élément à analyser/i)).toBeInTheDocument()
  })

  it('shows a refusal in words', async () => {
    await open([TILES, { path: '/analytics/heatmap', status: 422, body: { error: 'invalid_analysis', detail: 'dimension inconnue' } }])

    expect(await screen.findByRole('alert')).toHaveTextContent('dimension inconnue')
  })
})
