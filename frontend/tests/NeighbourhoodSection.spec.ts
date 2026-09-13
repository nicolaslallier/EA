import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Router } from 'vue-router'
import { createMemoryHistory } from 'vue-router'

import NeighbourhoodSection from '../src/features/neighbourhood/NeighbourhoodSection.vue'
import { createAppRouter } from '../src/router'
import {
  aGraph,
  anElement,
  aPage,
  aRelationship,
  METAMODEL,
  stubApi,
  type Route,
} from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const SUBJECT = anElement({ id: 'aaaaaaaa-1111-4111-8111-111111111111', name: 'Facturation' })
const NEIGHBOUR = anElement({
  id: 'bbbbbbbb-2222-4222-8222-222222222222',
  name: 'Commandes',
  element_type: 'business_process',
  layer: 'business',
})

const AROUND: Route = {
  path: `/elements/${SUBJECT.id}/neighbourhood`,
  body: aGraph(
    [SUBJECT, NEIGHBOUR],
    [aRelationship({ source_id: SUBJECT.id, target_id: NEIGHBOUR.id })],
  ),
}
const AROUND_NEIGHBOUR: Route = {
  path: `/elements/${NEIGHBOUR.id}/neighbourhood`,
  body: aGraph([NEIGHBOUR], []),
}
const CATALOGUE: Route = { path: '/elements', body: aPage([SUBJECT, NEIGHBOUR]) }
const PALETTE: Route = { path: '/metamodel', body: METAMODEL }

const ROUTES = [CATALOGUE, PALETTE, AROUND, AROUND_NEIGHBOUR]

async function open(query = ''): Promise<Router> {
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/analyse/voisinage${query}`)
  await router.isReady()
  render(NeighbourhoodSection, { global: { plugins: [router] } })
  return router
}

describe('NeighbourhoodSection', () => {
  it('asks which element before drawing anything', async () => {
    stubApi(ROUTES)
    await open()

    expect(await screen.findByText(/choisis un élément pour voir ce qui l'entoure/i)).toBeInTheDocument()
    expect(screen.queryByRole('figure')).toBeNull()
  })

  it('draws the sub-graph of the element named in the URL', async () => {
    stubApi(ROUTES)
    await open(`?element=${SUBJECT.id}`)

    expect(await screen.findByText(`Voisinage de « ${SUBJECT.name} »`)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: `Centrer sur ${NEIGHBOUR.name}` })).toBeInTheDocument()
  })

  it('puts the chosen element in the URL, so the view can be sent to someone', async () => {
    stubApi(ROUTES)
    const router = await open()
    await screen.findByRole('option', { name: SUBJECT.name })

    await fireEvent.update(screen.getByLabelText(/^élément$/i), SUBJECT.id)

    await waitFor(() => expect(router.currentRoute.value.query.element).toBe(SUBJECT.id))
    expect(await screen.findByText(`Voisinage de « ${SUBJECT.name} »`)).toBeInTheDocument()
  })

  it('walks the traversal deeper without a second screen', async () => {
    const calls = stubApi(ROUTES)
    await open(`?element=${SUBJECT.id}`)
    await screen.findByText(`Voisinage de « ${SUBJECT.name} »`)

    await fireEvent.update(screen.getByLabelText(/profondeur/i), '3')

    await waitFor(() => expect(calls.at(-1)?.url.searchParams.get('depth')).toBe('3'))
  })

  it('follows one relationship type when asked to', async () => {
    const calls = stubApi(ROUTES)
    await open(`?element=${SUBJECT.id}`)
    await screen.findByText(`Voisinage de « ${SUBJECT.name} »`)

    await fireEvent.update(screen.getByLabelText(/relation suivie/i), 'serving')

    await waitFor(() =>
      expect(calls.at(-1)?.url.searchParams.getAll('relationship_type')).toEqual(['serving']),
    )
  })

  it('moves the centre onto the neighbour that was clicked', async () => {
    stubApi(ROUTES)
    const router = await open(`?element=${SUBJECT.id}`)
    await screen.findByText(`Voisinage de « ${SUBJECT.name} »`)

    await fireEvent.click(screen.getByRole('button', { name: `Centrer sur ${NEIGHBOUR.name}` }))

    await waitFor(() => expect(router.currentRoute.value.query.element).toBe(NEIGHBOUR.id))
    expect(await screen.findByText(`Voisinage de « ${NEIGHBOUR.name} »`)).toBeInTheDocument()
  })

  it('leaves the previous centre in the history, so a walk can be walked back', async () => {
    stubApi(ROUTES)
    const router = await open(`?element=${SUBJECT.id}`)
    await screen.findByText(`Voisinage de « ${SUBJECT.name} »`)
    await fireEvent.click(screen.getByRole('button', { name: `Centrer sur ${NEIGHBOUR.name}` }))
    await waitFor(() => expect(router.currentRoute.value.query.element).toBe(NEIGHBOUR.id))

    router.back()

    await waitFor(() => expect(router.currentRoute.value.query.element).toBe(SUBJECT.id))
  })

  it('says so when the element has no neighbour at that depth', async () => {
    stubApi(ROUTES)
    await open(`?element=${NEIGHBOUR.id}`)

    expect(await screen.findByText(/n'a aucun voisin à 1 saut/i)).toBeInTheDocument()
  })

  it('reports a refused traversal instead of an empty canvas', async () => {
    stubApi([
      CATALOGUE,
      PALETTE,
      { path: AROUND.path, status: 404, body: { error: 'not_found', detail: 'Élément introuvable.' } },
    ])
    await open(`?element=${SUBJECT.id}`)

    expect(await screen.findByRole('alert')).toHaveTextContent('Élément introuvable.')
  })

  it('reports an unreachable backend rather than an empty picker', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )
    await open()

    expect(await screen.findByText(/injoignable/i)).toBeInTheDocument()
  })
})
