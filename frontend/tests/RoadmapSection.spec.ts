import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import RoadmapSection from '../src/features/roadmap/RoadmapSection.vue'
import { createAppRouter } from '../src/router'
import { aPlateau, aRoadmapItem, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const PHASE = aRoadmapItem({
  id: 'eeeeeeee-1111-4111-8111-111111111111',
  name: 'Phase 1',
  valid_from: '2026-02-01',
  valid_until: '2026-05-31',
})

async function open(body: unknown, query = '') {
  stubApi([
    { path: '/roadmap', body },
    { path: '/plateaus', body: [aPlateau()] },
  ])
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/analyse/feuille-de-route${query}`)
  await router.isReady()
  render(RoadmapSection, { global: { plugins: [router] } })
  return router
}

const DATED = { items: [PHASE], edges: [], window_from: '2026-02-01', window_to: '2026-05-31' }

describe('RoadmapSection', () => {
  it('draws the roadmap and keeps the comparison below it', async () => {
    await open(DATED)

    expect(await screen.findByRole('button', { name: /Phase 1/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /comparer deux plateaux/i })).toBeInTheDocument()
  })

  it('says so when nothing carries a date', async () => {
    await open({
      items: [aRoadmapItem({ name: 'Runbook', element_type: 'deliverable' })],
      edges: [],
      window_from: null,
      window_to: null,
    })

    expect(await screen.findByText(/aucune date saisie/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Runbook' })).toBeInTheDocument()
  })

  it('opens the element detail in the catalogue when a bar is chosen', async () => {
    const router = await open(DATED)

    await fireEvent.click(await screen.findByRole('button', { name: /Phase 1/ }))

    await waitFor(() => {
      expect(router.currentRoute.value.path).toBe('/elements')
      expect(router.currentRoute.value.query.element).toBe(PHASE.id)
    })
  })

  it('shows the as-of line when the URL names a day inside the window', async () => {
    await open(DATED, '?asof=2026-03-15')

    expect(await screen.findByTestId('asof-line')).toBeInTheDocument()
  })

  it("shows the API's refusal", async () => {
    stubApi([
      {
        path: '/roadmap',
        status: 422,
        body: { error: 'scope_too_large', detail: 'the roadmap holds more than 1000 items' },
      },
      { path: '/plateaus', body: [] },
    ])
    const router = createAppRouter(createMemoryHistory())
    await router.push('/analyse/feuille-de-route')
    await router.isReady()
    render(RoadmapSection, { global: { plugins: [router] } })

    expect(await screen.findByRole('alert')).toHaveTextContent('more than 1000')
  })
})
