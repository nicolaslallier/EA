import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'
import { createMemoryHistory } from 'vue-router'

import GapPanel from '../src/features/roadmap/GapPanel.vue'
import { useMe } from '../src/lib/me'
import { createAppRouter } from '../src/router'
import { anElement, aPlateau, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const AS_IS = aPlateau({ id: 'aaaaaaaa-1111-4111-8111-111111111111', name: 'As-Is' })
const TO_BE = aPlateau({
  id: 'bbbbbbbb-2222-4222-8222-222222222222',
  name: 'To-Be',
  valid_from: '2028-01-01',
})
const LEAVING = anElement({ id: '11111111-aaaa-4aaa-8aaa-111111111111', name: 'Legacy' })
const ARRIVING = anElement({ id: '22222222-aaaa-4aaa-8aaa-222222222222', name: 'Nouveau' })
const KEPT = anElement({ id: '33333333-aaaa-4aaa-8aaa-333333333333', name: 'Socle' })

const PLATEAUS: Route = { path: '/plateaus', body: [AS_IS, TO_BE] }
const COMPARISON: Route = {
  path: '/plateaus/gap',
  body: {
    a: anElement({ id: AS_IS.id, name: 'As-Is', element_type: 'plateau' }),
    b: anElement({ id: TO_BE.id, name: 'To-Be', element_type: 'plateau' }),
    only_in_a: [LEAVING],
    only_in_b: [ARRIVING],
    common: [KEPT],
    gaps: [],
  },
}

async function open(query = '', routes: Route[] = [PLATEAUS, COMPARISON]) {
  stubApi(routes)
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/analyse/feuille-de-route${query}`)
  await router.isReady()
  render(GapPanel, { global: { plugins: [router] } })
  return router
}

describe('GapPanel', () => {
  it('asks for two plateaus before comparing anything', async () => {
    await open()

    expect(await screen.findByText(/choisis deux plateaux/i)).toBeInTheDocument()
  })

  it('keeps the two plateaus in the URL', async () => {
    const router = await open()
    await screen.findAllByRole('option', { name: 'As-Is' })

    await fireEvent.update(screen.getByLabelText('Plateau de départ'), AS_IS.id)
    await waitFor(() => expect(router.currentRoute.value.query.a).toBe(AS_IS.id))
    await fireEvent.update(screen.getByLabelText("Plateau d'arrivée"), TO_BE.id)

    await waitFor(() => {
      expect(router.currentRoute.value.query).toMatchObject({ a: AS_IS.id, b: TO_BE.id })
    })
  })

  it('lists what leaves, what arrives and what stays', async () => {
    await open(`?a=${AS_IS.id}&b=${TO_BE.id}`)

    expect(await screen.findByRole('heading', { name: /sorties/i })).toBeInTheDocument()
    expect(screen.getByText('Legacy')).toBeInTheDocument()
    expect(screen.getByText('Nouveau')).toBeInTheDocument()
    expect(screen.getByText('Socle')).toBeInTheDocument()
  })

  it('offers an editor to create the gap', async () => {
    await open(`?a=${AS_IS.id}&b=${TO_BE.id}`)

    expect(await screen.findByRole('button', { name: "Créer l'écart" })).toBeInTheDocument()
  })

  it('hides the button from a reader', async () => {
    vi.mocked(useMe).mockReturnValueOnce({
      me: ref({ username: 'reader', can_write: false, can_approve: false }),
      canWrite: computed(() => false),
      error: ref(null),
      load: vi.fn(() => Promise.resolve()),
    })
    await open(`?a=${AS_IS.id}&b=${TO_BE.id}`)
    await screen.findByRole('heading', { name: /sorties/i })

    expect(screen.queryByRole('button', { name: "Créer l'écart" })).toBeNull()
  })

  it('does not offer a second gap when one already links the pair', async () => {
    const withGap: Route = {
      path: '/plateaus/gap',
      body: {
        ...(COMPARISON.body as object),
        gaps: [
          anElement({
            id: '44444444-aaaa-4aaa-8aaa-444444444444',
            name: 'Écart As-Is → To-Be',
            element_type: 'gap',
          }),
        ],
      },
    }
    await open(`?a=${AS_IS.id}&b=${TO_BE.id}`, [PLATEAUS, withGap])

    expect(await screen.findByText('Écart As-Is → To-Be')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: "Créer l'écart" })).toBeNull()
  })

  it("shows the API's refusal when the same plateau is chosen twice", async () => {
    const refused: Route = {
      path: '/plateaus/gap',
      status: 422,
      body: { error: 'invalid_timeline', detail: 'a plateau cannot be compared with itself' },
    }
    await open(`?a=${AS_IS.id}&b=${AS_IS.id}`, [PLATEAUS, refused])

    expect(await screen.findByRole('alert')).toHaveTextContent('cannot be compared with itself')
  })
})
