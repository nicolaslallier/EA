import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import CoverageSection from '../src/features/requirements/CoverageSection.vue'
import { createAppRouter } from '../src/router'
import { aCoverage, aTrace, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const SANS_CONCEPTION = aTrace({ name: 'Chiffrer les données', gap: 'design' })
const SANS_TEST = aTrace({
  id: 'bbbbbbbb-2222-4222-8222-222222222222',
  name: 'Journaliser les accès',
  identifier: 'REQ-2',
  gap: 'verification',
  designed_by: [
    { id: 'cccccccc-3333-4333-8333-333333333333', element_type: 'application_component', name: 'Coffre', result: null },
  ],
})

const COVERAGE: Route = {
  path: '/requirements/coverage',
  body: aCoverage([SANS_CONCEPTION, SANS_TEST], {
    designed: 1,
    verified: 0,
    design_rate: 0.5,
    verification_rate: 0,
  }),
}

async function open(query = '') {
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/analyse/couverture${query}`)
  await router.isReady()
  render(CoverageSection, { global: { plugins: [router] } })
  return router
}

describe('CoverageSection', () => {
  it('lists the requirements with the gap each one has', async () => {
    stubApi([COVERAGE])
    await open()

    expect(await screen.findByText('Chiffrer les données')).toBeInTheDocument()
    expect(screen.getByText('Non réalisée')).toBeInTheDocument()
    expect(screen.getByText('Non testée')).toBeInTheDocument()
    expect(screen.getByText('Coffre')).toBeInTheDocument()
    expect(screen.getByText('50 %')).toBeInTheDocument()
  })

  it('links a requirement to its detail in the catalogue', async () => {
    stubApi([COVERAGE])
    await open()

    const link = await screen.findByRole('link', { name: 'Chiffrer les données' })

    expect(link).toHaveAttribute('href', `/elements?element=${SANS_CONCEPTION.id}`)
  })

  it('shows a dash instead of a rate when there is nothing to measure', async () => {
    stubApi([{ path: '/requirements/coverage', body: aCoverage([]) }])
    await open()

    expect(await screen.findByText('Aucune exigence dans le catalogue.')).toBeInTheDocument()
    expect(screen.getAllByText('—')).toHaveLength(2)
  })

  it('offers the statuses the answer carries and puts the choice in the URL', async () => {
    const calls = stubApi([COVERAGE])
    const router = await open()
    await screen.findByText('Chiffrer les données')

    await fireEvent.update(screen.getByLabelText('Statut'), 'approved')

    await waitFor(() => expect(router.currentRoute.value.query.status).toBe('approved'))
    await waitFor(() => expect(calls.at(-1)?.url.searchParams.get('status')).toBe('approved'))
  })

  it('asks for the status named in the URL on arrival', async () => {
    const calls = stubApi([COVERAGE])
    await open('?status=proposed&priority=must')

    await screen.findByText('Chiffrer les données')

    expect(calls[0].url.searchParams.get('status')).toBe('proposed')
    expect(calls[0].url.searchParams.get('priority')).toBe('must')
  })

  it('says why it could not load', async () => {
    stubApi([
      { path: '/requirements/coverage', status: 500, body: { error: 'internal', detail: 'Le graphe est tombé.' } },
    ])
    await open()

    expect(await screen.findByRole('alert')).toHaveTextContent('Le graphe est tombé.')
  })
})
