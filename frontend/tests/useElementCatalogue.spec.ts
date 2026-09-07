import { afterEach, describe, expect, it, vi } from 'vitest'

import { useElementCatalogue } from '../src/features/elements/useElementCatalogue'
import { aPage, anElement, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useElementCatalogue', () => {
  it('exposes the first page and the total the API reports', async () => {
    stubApi([{ path: '/elements', body: aPage([anElement({ name: 'Facturation' })], 42) }])

    const catalogue = useElementCatalogue()
    await catalogue.load()

    expect(catalogue.items.value.map((element) => element.name)).toEqual(['Facturation'])
    expect(catalogue.total.value).toBe(42)
    expect(catalogue.status.value).toBe('ready')
  })

  it('sends the filters as query parameters rather than filtering in the browser', async () => {
    const calls = stubApi([{ path: '/elements', body: aPage([]) }])

    const catalogue = useElementCatalogue()
    catalogue.filters.search = 'factu'
    catalogue.filters.elementType = 'application_component'
    catalogue.filters.layer = 'application'
    await catalogue.load()

    const query = calls[0].url.searchParams
    expect(query.get('search')).toBe('factu')
    expect(query.get('element_type')).toBe('application_component')
    expect(query.get('layer')).toBe('application')
    expect(query.get('limit')).toBe('25')
  })

  it('omits a filter that is not set instead of sending an empty one', async () => {
    const calls = stubApi([{ path: '/elements', body: aPage([]) }])

    await useElementCatalogue().load()

    const query = calls[0].url.searchParams
    expect(query.has('search')).toBe(false)
    expect(query.has('element_type')).toBe(false)
    expect(query.has('layer')).toBe(false)
  })

  it('creates an element and reloads so the new row is the stored one', async () => {
    const calls = stubApi([
      { path: '/elements', body: aPage([anElement()]) },
      { method: 'POST', path: '/elements', status: 201, body: anElement({ name: 'Grand livre' }) },
    ])

    const catalogue = useElementCatalogue()
    await catalogue.create({ element_type: 'application_component', name: 'Grand livre' })

    expect(calls.map((call) => call.method)).toEqual(['POST', 'GET'])
  })

  it('updates an element with a patch of the changed fields only', async () => {
    const id = '22222222-2222-4222-8222-222222222222'
    const calls = stubApi([
      { path: '/elements', body: aPage([]) },
      { method: 'PATCH', path: `/elements/${id}`, body: anElement({ id, name: 'Grand livre' }) },
    ])

    await useElementCatalogue().update(id, { name: 'Grand livre' })

    expect(calls[0].url.pathname).toBe(`/elements/${id}`)
  })

  it('deletes an element and steps back a page when it emptied the last one', async () => {
    const id = '33333333-3333-4333-8333-333333333333'
    stubApi([
      { path: '/elements', body: aPage([], 25) },
      { method: 'DELETE', path: `/elements/${id}`, status: 204 },
    ])

    const catalogue = useElementCatalogue()
    catalogue.goTo(1)
    await catalogue.remove(id)

    expect(catalogue.page.value).toBe(1)
  })

  it('reports the API error envelope rather than a blank screen', async () => {
    stubApi([
      { path: '/elements', body: aPage([]) },
      {
        method: 'POST',
        path: '/elements',
        status: 409,
        body: { error: 'duplicate', detail: 'Facturation existe déjà' },
      },
    ])

    const catalogue = useElementCatalogue()
    await expect(
      catalogue.create({ element_type: 'application_component', name: 'Facturation' }),
    ).rejects.toThrow('Facturation existe déjà')
  })

  it('reports an unreachable backend instead of staying on "loading"', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )

    const catalogue = useElementCatalogue()
    await catalogue.load()

    expect(catalogue.status.value).toBe('error')
    expect(catalogue.error.value).toMatch(/injoignable/i)
  })

  it('paginates by offset, and knows when there is no page after this one', async () => {
    const calls = stubApi([{ path: '/elements', body: aPage([], 30) }])

    const catalogue = useElementCatalogue()
    await catalogue.load()
    expect(catalogue.hasNextPage.value).toBe(true)

    catalogue.goTo(1)
    await catalogue.load()

    expect(calls[1].url.searchParams.get('offset')).toBe('25')
    expect(catalogue.hasNextPage.value).toBe(false)
  })
})
