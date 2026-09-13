import { afterEach, describe, expect, it, vi } from 'vitest'

import { useImpact } from '../src/features/impact/useImpact'
import { aGraph, anElement, aRelationship, deferApi, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const SUBJECT = anElement({ id: 'aaaaaaaa-1111-4111-8111-111111111111', name: 'Facturation' })
const DEPENDENT = anElement({
  id: 'bbbbbbbb-2222-4222-8222-222222222222',
  name: 'Order to cash',
  element_type: 'business_process',
  layer: 'business',
})

const ROUTE: Route = {
  path: `/elements/${SUBJECT.id}/impact`,
  body: aGraph(
    [SUBJECT, DEPENDENT],
    [aRelationship({ source_id: SUBJECT.id, target_id: DEPENDENT.id })],
  ),
}

/** What `/metamodel` says: a serving link carries an outage along its arrow. */
const follows = () => true

describe('useImpact', () => {
  it('asks the impact traversal, not the neighbourhood, for the given depth', async () => {
    const calls = stubApi([ROUTE])
    const impact = useImpact(follows)

    await impact.analyse(SUBJECT.id, { depth: 4 })

    expect(calls.at(-1)?.url.pathname).toBe(`/elements/${SUBJECT.id}/impact`)
    expect(calls.at(-1)?.url.searchParams.get('depth')).toBe('4')
    expect(impact.status.value).toBe('ready')
  })

  it('follows every relationship type unless one is asked for', async () => {
    const calls = stubApi([ROUTE])
    const impact = useImpact(follows)

    await impact.analyse(SUBJECT.id, { depth: 5, relationshipType: '' })
    expect(calls.at(-1)?.url.searchParams.has('relationship_type')).toBe(false)

    await impact.analyse(SUBJECT.id, { depth: 5, relationshipType: 'serving' })
    expect(calls.at(-1)?.url.searchParams.getAll('relationship_type')).toEqual(['serving'])
  })

  it('reads the subject out of the answer rather than asking for it again', async () => {
    const calls = stubApi([ROUTE])
    const impact = useImpact(follows)

    await impact.analyse(SUBJECT.id, { depth: 5 })

    expect(impact.subject.value?.name).toBe(SUBJECT.name)
    expect(calls).toHaveLength(1)
  })

  it('ranks what it reached by distance, and counts it', async () => {
    stubApi([ROUTE])
    const impact = useImpact(follows)

    await impact.analyse(SUBJECT.id, { depth: 5 })

    expect(impact.impacted.value).toBe(1)
    expect(impact.waves.value).toEqual([{ hops: 1, elements: [DEPENDENT] }])
  })

  it('leaves out of the cascade what the metamodel says an outage cannot reach', async () => {
    stubApi([ROUTE])
    // The same answer read with the opposite direction rule: nothing is downstream.
    const impact = useImpact(() => false)

    await impact.analyse(SUBJECT.id, { depth: 5 })

    expect(impact.impacted.value).toBe(0)
    expect(impact.waves.value).toEqual([])
  })

  it('reports a refused traversal and keeps no stale cascade on screen', async () => {
    stubApi([ROUTE])
    const impact = useImpact(follows)
    await impact.analyse(SUBJECT.id, { depth: 5 })

    stubApi([
      { path: ROUTE.path, status: 404, body: { error: 'not_found', detail: 'Élément introuvable.' } },
    ])
    await impact.analyse(SUBJECT.id, { depth: 5 })

    expect(impact.status.value).toBe('error')
    expect(impact.error.value).toBe('Élément introuvable.')
    expect(impact.graph.value.elements).toHaveLength(0)
    expect(impact.waves.value).toEqual([])
  })

  it('shows the cascade of the element asked for last, whichever answer arrives first', async () => {
    const calls = deferApi()
    const impact = useImpact(follows)

    const first = impact.analyse(SUBJECT.id, { depth: 5 })
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    const second = impact.analyse(DEPENDENT.id, { depth: 5 })
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    calls[1].answer(aGraph([DEPENDENT], []))
    calls[0].answer(ROUTE.body)
    await Promise.all([first, second])

    expect(calls[0].aborted()).toBe(true)
    expect(impact.subject.value?.name).toBe(DEPENDENT.name)
    expect(impact.impacted.value).toBe(0)
    expect(impact.status.value).toBe('ready')
    expect(impact.error.value).toBe('')
  })

  it('reports an unreachable backend rather than an empty cascade', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )
    const impact = useImpact(follows)

    await impact.analyse(SUBJECT.id, { depth: 5 })

    expect(impact.error.value).toMatch(/injoignable/i)
  })
})
