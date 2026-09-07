import { fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import RelationshipsSection from '../src/features/relationships/RelationshipsSection.vue'
import { aGraph, aPage, anElement, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const API = anElement({
  id: 'aaaaaaaa-1111-4111-8111-111111111111',
  element_type: 'application_service',
  name: 'Invoice API',
})

const CATALOGUE: Route = { path: '/elements', body: aPage([API]) }
const RELATIONS: Route = {
  path: `/elements/${API.id}/relationships`,
  body: aGraph([API], []),
}

describe('RelationshipsSection', () => {
  it('asks which element before showing any relation', async () => {
    stubApi([CATALOGUE, RELATIONS])
    render(RelationshipsSection)

    expect(await screen.findByText(/choisis un élément pour voir ses relations/i)).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: /relations de l'élément/i })).not.toBeInTheDocument()
  })

  it('shows the relations of the element once one is picked', async () => {
    stubApi([CATALOGUE, RELATIONS])
    render(RelationshipsSection)

    await screen.findByRole('option', { name: API.name })
    await fireEvent.update(screen.getByLabelText(/^élément$/i), API.id)

    expect(
      await screen.findByRole('region', { name: /relations de l'élément/i }),
    ).toBeInTheDocument()
  })

  it('searches the catalogue on the server rather than in the browser', async () => {
    const calls = stubApi([CATALOGUE, RELATIONS])
    render(RelationshipsSection)
    await screen.findByRole('option', { name: API.name })

    await fireEvent.update(screen.getByLabelText(/rechercher/i), 'invoice')
    await fireEvent.submit(screen.getByRole('search'))

    expect(calls.at(-1)?.url.searchParams.get('search')).toBe('invoice')
  })

  it('reports an unreachable backend instead of an empty picker', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )
    render(RelationshipsSection)

    expect(await screen.findByText(/injoignable/i)).toBeInTheDocument()
  })
})
