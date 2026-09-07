import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import RelationshipPanel from '../src/features/relationships/RelationshipPanel.vue'
import { aGraph, aPage, aRelationship, anElement, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const API = anElement({
  id: 'aaaaaaaa-1111-4111-8111-111111111111',
  element_type: 'application_service',
  name: 'Invoice API',
})
const PROCESS = anElement({
  id: 'bbbbbbbb-2222-4222-8222-222222222222',
  element_type: 'business_process',
  layer: 'business',
  name: 'Order to cash',
})

const RELATIONS: Route = {
  path: `/elements/${API.id}/relationships`,
  body: aGraph([API, PROCESS], [aRelationship({ source_id: API.id, target_id: PROCESS.id })]),
}
const NO_RELATION: Route = {
  path: `/elements/${API.id}/relationships`,
  body: aGraph([API], []),
}
const CANDIDATES: Route = { path: '/elements', body: aPage([PROCESS]) }
const PERMITTED: Route = { path: '/metamodel/relationships', body: ['serving', 'association'] }

function renderPanel(routes: Route[]) {
  const calls = stubApi(routes)
  return { ...render(RelationshipPanel, { props: { element: API } }), calls }
}

/** Pick the other end of the link, once the candidates have arrived. */
async function chooseOther() {
  await screen.findByRole('option', { name: PROCESS.name })
  await fireEvent.update(screen.getByLabelText(/autre élément/i), PROCESS.id)
}

/** Fill the form: pick the other end, then the relationship it may carry. */
async function fillForm(relationship = 'serving') {
  await chooseOther()
  await waitFor(() => expect(screen.getByLabelText(/type de relation/i)).not.toBeDisabled())
  await fireEvent.update(screen.getByLabelText(/type de relation/i), relationship)
}

describe('RelationshipPanel', () => {
  it('reads each link as a sentence with both element names', async () => {
    renderPanel([RELATIONS, CANDIDATES])

    const row = within(await screen.findByRole('row', { name: /Invoice API/ }))
    expect(row.getByText('Invoice API')).toBeInTheDocument()
    expect(row.getByText('Order to cash')).toBeInTheDocument()
    expect(row.getByText(/sert/i)).toBeInTheDocument()
  })

  it('says so plainly when an element is linked to nothing yet', async () => {
    renderPanel([NO_RELATION, CANDIDATES])

    expect(await screen.findByText(/n'est encore associé à rien/i)).toBeInTheDocument()
  })

  it('offers only the relationships the metamodel permits for the chosen pair', async () => {
    const { calls } = renderPanel([NO_RELATION, CANDIDATES, PERMITTED])

    await fillForm()

    const query = calls[calls.length - 1].url.searchParams
    expect(query.get('source')).toBe('application_service')
    expect(query.get('target')).toBe('business_process')
    const options = within(screen.getByLabelText(/type de relation/i)).getAllByRole('option')
    expect(options.map((option) => option.textContent?.trim())).toEqual([
      'Choisis une relation…',
      'sert',
      'est associé à',
    ])
  })

  it('asks the question the other way round when the direction is reversed', async () => {
    const { calls } = renderPanel([NO_RELATION, CANDIDATES, PERMITTED])

    await chooseOther()
    await fireEvent.update(screen.getByLabelText(/sens/i), 'incoming')

    await waitFor(() => {
      const query = calls[calls.length - 1].url.searchParams
      expect(query.get('source')).toBe('business_process')
      expect(query.get('target')).toBe('application_service')
    })
  })

  it('refuses to submit a pair the metamodel allows nothing for, and says why', async () => {
    renderPanel([NO_RELATION, CANDIDATES, { path: '/metamodel/relationships', body: [] }])

    await chooseOther()

    expect(await screen.findByText(/aucune relation n'est permise/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^associer$/i })).toBeDisabled()
  })

  it('reverses the payload when the link points at the element', async () => {
    const { calls } = renderPanel([
      NO_RELATION,
      CANDIDATES,
      PERMITTED,
      { method: 'POST', path: '/relationships', status: 201, body: aRelationship() },
    ])

    await chooseOther()
    await fireEvent.update(screen.getByLabelText(/sens/i), 'incoming')
    await waitFor(() => expect(screen.getByLabelText(/type de relation/i)).not.toBeDisabled())
    await fireEvent.update(screen.getByLabelText(/type de relation/i), 'serving')
    await fireEvent.click(screen.getByRole('button', { name: /^associer$/i }))

    await waitFor(() => expect(calls.some((call) => call.method === 'POST')).toBe(true))
    expect(calls.find((call) => call.method === 'POST')?.body).toMatchObject({
      source_id: PROCESS.id,
      target_id: API.id,
    })
  })

  it('posts the link in the direction the form describes', async () => {
    const { calls } = renderPanel([
      NO_RELATION,
      CANDIDATES,
      PERMITTED,
      { method: 'POST', path: '/relationships', status: 201, body: aRelationship() },
    ])

    await fillForm()
    await fireEvent.click(screen.getByRole('button', { name: /^associer$/i }))

    await waitFor(() => expect(calls.some((call) => call.method === 'POST')).toBe(true))
    const posted = calls.find((call) => call.method === 'POST')?.body
    expect(posted).toMatchObject({
      relationship_type: 'serving',
      source_id: API.id,
      target_id: PROCESS.id,
    })
  })

  it('asks the qualifier an access relationship needs, and only then', async () => {
    renderPanel([
      NO_RELATION,
      CANDIDATES,
      { path: '/metamodel/relationships', body: ['access', 'serving'] },
    ])

    await fillForm()
    expect(screen.queryByLabelText(/mode d'accès/i)).not.toBeInTheDocument()

    await fireEvent.update(screen.getByLabelText(/type de relation/i), 'access')

    expect(await screen.findByLabelText(/mode d'accès/i)).toBeInTheDocument()
  })

  it('confirms before removing a link rather than deleting on one click', async () => {
    const { calls } = renderPanel([
      RELATIONS,
      CANDIDATES,
      { method: 'DELETE', path: `/relationships/${aRelationship().id}`, status: 204 },
    ])

    await fireEvent.click(await screen.findByRole('button', { name: /dissocier/i }))
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)

    await fireEvent.click(screen.getByRole('button', { name: /confirmer/i }))
    await waitFor(() => expect(calls.some((call) => call.method === 'DELETE')).toBe(true))
  })

  it('keeps what the API refused on screen instead of a blank panel', async () => {
    renderPanel([
      NO_RELATION,
      CANDIDATES,
      PERMITTED,
      {
        method: 'POST',
        path: '/relationships',
        status: 409,
        body: { error: 'cyclic_containment', detail: 'Cela fermerait une boucle.' },
      },
    ])

    await fillForm()
    await fireEvent.click(screen.getByRole('button', { name: /^associer$/i }))

    expect(await screen.findByText(/cela fermerait une boucle/i)).toBeInTheDocument()
  })
})
