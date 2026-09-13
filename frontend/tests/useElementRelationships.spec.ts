import { afterEach, describe, expect, it, vi } from 'vitest'

import { useElementRelationships } from '../src/features/relationships/useElementRelationships'
import { aGraph, aPage, aRelationship, anElement, deferApi, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const API = anElement({ id: 'aaaaaaaa-1111-4111-8111-111111111111', name: 'Invoice API' })
const PROCESS = anElement({
  id: 'bbbbbbbb-2222-4222-8222-222222222222',
  element_type: 'business_process',
  layer: 'business',
  name: 'Order to cash',
})

describe('useElementRelationships', () => {
  it('asks for the relations of one element, endpoints included', async () => {
    const calls = stubApi([
      {
        path: `/elements/${API.id}/relationships`,
        body: aGraph([API, PROCESS], [aRelationship({ source_id: API.id, target_id: PROCESS.id })]),
      },
    ])

    const relations = useElementRelationships()
    await relations.open(API)

    expect(calls[0].url.pathname).toBe(`/elements/${API.id}/relationships`)
    expect(relations.links.value).toHaveLength(1)
    expect(relations.status.value).toBe('ready')
  })

  it('names both ends of a link so a row reads as a sentence', async () => {
    stubApi([
      {
        path: `/elements/${API.id}/relationships`,
        body: aGraph([API, PROCESS], [aRelationship({ source_id: API.id, target_id: PROCESS.id })]),
      },
    ])

    const relations = useElementRelationships()
    await relations.open(API)

    expect(relations.nameOf(API.id)).toBe('Invoice API')
    expect(relations.nameOf(PROCESS.id)).toBe('Order to cash')
  })

  it('falls back to a placeholder rather than showing a raw id', async () => {
    stubApi([{ path: `/elements/${API.id}/relationships`, body: aGraph([API], []) }])

    const relations = useElementRelationships()
    await relations.open(API)

    expect(relations.nameOf('unknown-id')).toBe('élément inconnu')
  })

  it('asks the backend which relationships the metamodel permits, in order', async () => {
    const calls = stubApi([
      { path: `/elements/${API.id}/relationships`, body: aGraph([API], []) },
      { path: '/metamodel/relationships', body: ['serving', 'association'] },
    ])

    const relations = useElementRelationships()
    await relations.open(API)
    await relations.loadPermitted('application_service', 'business_process')

    const query = calls[1].url.searchParams
    expect(query.get('source')).toBe('application_service')
    expect(query.get('target')).toBe('business_process')
    expect(relations.permitted.value).toEqual(['serving', 'association'])
  })

  it('reports an illegal pair as an empty choice rather than a crash', async () => {
    stubApi([
      { path: `/elements/${API.id}/relationships`, body: aGraph([API], []) },
      { path: '/metamodel/relationships', body: [] },
    ])

    const relations = useElementRelationships()
    await relations.open(API)
    await relations.loadPermitted('business_process', 'application_service')

    expect(relations.permitted.value).toEqual([])
  })

  it('creates a link and reloads so the list shows what was stored', async () => {
    const calls = stubApi([
      { path: `/elements/${API.id}/relationships`, body: aGraph([API], []) },
      { method: 'POST', path: '/relationships', status: 201, body: aRelationship() },
    ])

    const relations = useElementRelationships()
    await relations.open(API)
    await relations.connect({
      relationship_type: 'serving',
      source_id: API.id,
      target_id: PROCESS.id,
    })

    expect(calls.map((call) => call.method)).toEqual(['GET', 'POST', 'GET'])
  })

  it('deletes a link and reloads', async () => {
    const link = aRelationship({ id: 'cccccccc-3333-4333-8333-333333333333' })
    const calls = stubApi([
      { path: `/elements/${API.id}/relationships`, body: aGraph([API, PROCESS], [link]) },
      { method: 'DELETE', path: `/relationships/${link.id}`, status: 204 },
    ])

    const relations = useElementRelationships()
    await relations.open(API)
    await relations.disconnect(link.id)

    expect(calls.map((call) => call.method)).toEqual(['GET', 'DELETE', 'GET'])
  })

  it('searches candidate elements on the server, never in the browser', async () => {
    const calls = stubApi([
      { path: `/elements/${API.id}/relationships`, body: aGraph([API], []) },
      { path: '/elements', body: aPage([PROCESS]) },
    ])

    const relations = useElementRelationships()
    await relations.open(API)
    await relations.searchCandidates('order')

    expect(calls[1].url.searchParams.get('search')).toBe('order')
    expect(relations.candidates.value.map((element) => element.name)).toEqual(['Order to cash'])
  })

  it('does not offer the element itself as the other end of the link', async () => {
    stubApi([
      { path: `/elements/${API.id}/relationships`, body: aGraph([API], []) },
      { path: '/elements', body: aPage([API, PROCESS]) },
    ])

    const relations = useElementRelationships()
    await relations.open(API)
    await relations.searchCandidates('')

    expect(relations.candidates.value.map((element) => element.id)).toEqual([PROCESS.id])
  })

  it('offers the candidates of the last term typed, whichever answer arrives first', async () => {
    const calls = deferApi()
    const relations = useElementRelationships()

    const first = relations.searchCandidates('s')
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    const second = relations.searchCandidates('ser')
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    calls[1].answer(aPage([PROCESS]))
    calls[0].answer(aPage([API, PROCESS, anElement({ id: 'x', name: 'Stock' })]))
    await Promise.all([first, second])

    expect(calls[1].url.searchParams.get('search')).toBe('ser')
    expect(relations.candidates.value.map((element) => element.name)).toEqual(['Order to cash'])
    expect(relations.lookupError.value).toBe('')
  })

  it('offers the relationships of the last pair asked about, whichever answer arrives first', async () => {
    const calls = deferApi()
    const relations = useElementRelationships()

    const first = relations.loadPermitted('application_service', 'business_process')
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    const second = relations.loadPermitted('business_process', 'application_service')
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    calls[1].answer([])
    calls[0].answer(['serving', 'association'])
    await Promise.all([first, second])

    expect(relations.permitted.value).toEqual([])
  })

  it('offers nothing late for a pair that was abandoned', async () => {
    const calls = deferApi()
    const relations = useElementRelationships()

    const pending = relations.loadPermitted('application_service', 'business_process')
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    relations.clearPermitted()
    calls[0].answer(['serving'])
    await pending

    expect(relations.permitted.value).toEqual([])
  })

  it('reports a failed candidate search itself rather than throwing at its caller', async () => {
    stubApi([
      { path: '/elements', status: 500, body: { error: 'internal', detail: 'Le graphe est tombé.' } },
    ])
    const relations = useElementRelationships()

    await expect(relations.searchCandidates('ord')).resolves.toBeUndefined()

    expect(relations.lookupError.value).toBe('Le graphe est tombé.')
    expect(relations.candidates.value).toEqual([])
  })

  it('reports a failed metamodel question itself rather than throwing at its caller', async () => {
    stubApi([
      { path: '/metamodel/relationships', status: 422, body: { error: 'invalid', detail: 'Type inconnu.' } },
    ])
    const relations = useElementRelationships()

    await expect(
      relations.loadPermitted('application_service', 'business_process'),
    ).resolves.toBeUndefined()

    expect(relations.lookupError.value).toBe('Type inconnu.')
    expect(relations.permitted.value).toEqual([])
  })

  it('reports a failure instead of leaving the panel blank', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )

    const relations = useElementRelationships()
    await relations.open(API)

    expect(relations.status.value).toBe('error')
    expect(relations.error.value).toMatch(/injoignable/i)
  })
})
