import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Router } from 'vue-router'
import { createMemoryHistory } from 'vue-router'

import ImpactSection from '../src/features/impact/ImpactSection.vue'
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
const PROCESS = anElement({
  id: 'bbbbbbbb-2222-4222-8222-222222222222',
  name: 'Order to cash',
  element_type: 'business_process',
  layer: 'business',
})
const CHAIN = anElement({ id: 'cccccccc-3333-4333-8333-333333333333', name: 'Chaîne de vente' })

// Facturation sert le processus, et la chaîne de vente le compose : la panne
// descend le long de la flèche, puis la remonte.
const CASCADE = aGraph(
  [SUBJECT, PROCESS, CHAIN],
  [
    aRelationship({ id: 'sert', source_id: SUBJECT.id, target_id: PROCESS.id }),
    aRelationship({
      id: 'compose',
      relationship_type: 'composition',
      source_id: CHAIN.id,
      target_id: PROCESS.id,
    }),
  ],
)

const IMPACT: Route = { path: `/elements/${SUBJECT.id}/impact`, body: CASCADE }
const IMPACT_OF_PROCESS: Route = {
  path: `/elements/${PROCESS.id}/impact`,
  body: aGraph([PROCESS], []),
}
const CATALOGUE: Route = { path: '/elements', body: aPage([SUBJECT, PROCESS, CHAIN]) }
const PALETTE: Route = { path: '/metamodel', body: METAMODEL }

const ROUTES = [CATALOGUE, PALETTE, IMPACT, IMPACT_OF_PROCESS]

async function open(query = ''): Promise<Router> {
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/analyse/impact${query}`)
  await router.isReady()
  render(ImpactSection, { global: { plugins: [router] } })
  return router
}

describe('ImpactSection', () => {
  it('asks which element before analysing anything', async () => {
    stubApi(ROUTES)
    await open()

    expect(await screen.findByText(/choisis un élément pour voir ce qui tombe/i)).toBeInTheDocument()
    expect(screen.queryByRole('figure')).toBeNull()
  })

  it('analyses the element named in the URL and says how much breaks', async () => {
    stubApi(ROUTES)
    await open(`?element=${SUBJECT.id}`)

    expect(await screen.findByText(`Impact de « ${SUBJECT.name} »`)).toBeInTheDocument()
    expect(screen.getByText(/2 éléments dépendent de/i)).toBeInTheDocument()
  })

  it('lists what breaks, wave by wave, nearest first', async () => {
    stubApi(ROUTES)
    await open(`?element=${SUBJECT.id}`)
    await screen.findByText(`Impact de « ${SUBJECT.name} »`)

    const waves = screen.getAllByRole('heading', { level: 4 })
    expect(waves.map((heading) => heading.textContent?.trim())).toEqual(['À 1 saut', 'À 2 sauts'])

    const cascade = screen.getByRole('list', { name: /éléments impactés/i })
    expect(cascade).toHaveTextContent(PROCESS.name)
    expect(cascade).toHaveTextContent(CHAIN.name)
  })

  it('walks a composition backwards, as the metamodel says an outage does', async () => {
    stubApi(ROUTES)
    await open(`?element=${SUBJECT.id}`)
    await screen.findByText(`Impact de « ${SUBJECT.name} »`)

    // The chain *composes* the process, so its arrow points the other way; it
    // is impacted all the same, two hops out.
    const second = screen.getByRole('heading', { level: 4, name: 'À 2 sauts' })
    expect(second.nextElementSibling).toHaveTextContent(CHAIN.name)
  })

  it('says plainly when nothing depends on the element', async () => {
    stubApi(ROUTES)
    await open(`?element=${PROCESS.id}`)

    expect(await screen.findByText(/rien ne dépend de/i)).toBeInTheDocument()
  })

  it('puts the chosen element in the URL, so the verdict can be sent to someone', async () => {
    stubApi(ROUTES)
    const router = await open()
    await screen.findByRole('option', { name: SUBJECT.name })

    await fireEvent.update(screen.getByLabelText(/^élément$/i), SUBJECT.id)

    await waitFor(() => expect(router.currentRoute.value.query.element).toBe(SUBJECT.id))
    expect(await screen.findByText(`Impact de « ${SUBJECT.name} »`)).toBeInTheDocument()
  })

  it('follows the cascade onto an impacted element clicked on the drawing', async () => {
    stubApi(ROUTES)
    const router = await open(`?element=${SUBJECT.id}`)

    await fireEvent.click(
      await screen.findByRole('button', { name: `Analyser l'impact de ${PROCESS.name}` }),
    )

    await waitFor(() => expect(router.currentRoute.value.query.element).toBe(PROCESS.id))
    expect(await screen.findByText(`Impact de « ${PROCESS.name} »`)).toBeInTheDocument()
  })

  it('leaves each subject in the history, and a turned dial out of it', async () => {
    stubApi(ROUTES)
    const router = await open(`?element=${SUBJECT.id}`)
    await fireEvent.click(
      await screen.findByRole('button', { name: `Analyser l'impact de ${PROCESS.name}` }),
    )
    await waitFor(() => expect(router.currentRoute.value.query.element).toBe(PROCESS.id))

    await fireEvent.update(screen.getByLabelText(/profondeur/i), '2')
    await waitFor(() => expect(router.currentRoute.value.query.depth).toBe('2'))

    // One step back is the previous *subject*: the depth replaced its entry
    // rather than burying it, or a dozen turns would hide the way back.
    router.back()
    await waitFor(() => expect(router.currentRoute.value.query.element).toBe(SUBJECT.id))
    expect(router.currentRoute.value.query.depth).toBeUndefined()
  })

  it('asks the traversal again for the depth the URL was changed to', async () => {
    const calls = stubApi(ROUTES)
    await open(`?element=${SUBJECT.id}`)
    await screen.findByText(`Impact de « ${SUBJECT.name} »`)

    await fireEvent.update(screen.getByLabelText(/profondeur/i), '2')

    await waitFor(() => expect(calls.at(-1)?.url.searchParams.get('depth')).toBe('2'))
  })

  it('follows only the relationship named in the URL', async () => {
    const calls = stubApi(ROUTES)
    await open(`?element=${SUBJECT.id}&relation=serving`)
    await screen.findByText(`Impact de « ${SUBJECT.name} »`)

    const traversal = calls.filter((call) => call.url.pathname === IMPACT.path).at(-1)
    expect(traversal?.url.searchParams.getAll('relationship_type')).toEqual(['serving'])
  })

  it('reports a refused traversal instead of drawing an empty cascade', async () => {
    stubApi([
      CATALOGUE,
      PALETTE,
      { path: IMPACT.path, status: 404, body: { error: 'not_found', detail: 'Élément introuvable.' } },
    ])
    await open(`?element=${SUBJECT.id}`)

    expect(await screen.findByRole('alert')).toHaveTextContent('Élément introuvable.')
    expect(screen.queryByRole('figure')).toBeNull()
  })
})
