import { afterEach, describe, expect, it, vi } from 'vitest'

import { useNeighbourhood } from '../src/features/neighbourhood/useNeighbourhood'
import { aGraph, anElement, aRelationship, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const SUBJECT = anElement({ id: 'aaaaaaaa-1111-4111-8111-111111111111', name: 'Facturation' })
const NEIGHBOUR = anElement({ id: 'bbbbbbbb-2222-4222-8222-222222222222', name: 'Commandes' })

const ROUTE: Route = {
  path: `/elements/${SUBJECT.id}/neighbourhood`,
  body: aGraph(
    [SUBJECT, NEIGHBOUR],
    [aRelationship({ source_id: SUBJECT.id, target_id: NEIGHBOUR.id })],
  ),
}

describe('useNeighbourhood', () => {
  it('asks the traversal for the depth it was given', async () => {
    const calls = stubApi([ROUTE])
    const neighbourhood = useNeighbourhood()

    await neighbourhood.explore(SUBJECT.id, { depth: 3 })

    expect(calls.at(-1)?.url.searchParams.get('depth')).toBe('3')
    expect(neighbourhood.status.value).toBe('ready')
  })

  it('follows every relationship type unless one is asked for', async () => {
    const calls = stubApi([ROUTE])
    const neighbourhood = useNeighbourhood()

    await neighbourhood.explore(SUBJECT.id, { depth: 1, relationshipType: '' })
    expect(calls.at(-1)?.url.searchParams.has('relationship_type')).toBe(false)

    await neighbourhood.explore(SUBJECT.id, { depth: 1, relationshipType: 'serving' })
    expect(calls.at(-1)?.url.searchParams.getAll('relationship_type')).toEqual(['serving'])
  })

  it('reads the subject out of the answer rather than asking for it again', async () => {
    const calls = stubApi([ROUTE])
    const neighbourhood = useNeighbourhood()

    await neighbourhood.explore(SUBJECT.id, { depth: 1 })

    expect(neighbourhood.subject.value?.name).toBe(SUBJECT.name)
    expect(neighbourhood.neighbours.value).toBe(1)
    expect(calls).toHaveLength(1)
  })

  it('counts no neighbour when the traversal returns the element alone', async () => {
    stubApi([{ path: ROUTE.path, body: aGraph([SUBJECT], []) }])
    const neighbourhood = useNeighbourhood()

    await neighbourhood.explore(SUBJECT.id, { depth: 1 })

    expect(neighbourhood.neighbours.value).toBe(0)
  })

  it('reports a refused traversal and keeps no stale drawing on screen', async () => {
    stubApi([ROUTE])
    const neighbourhood = useNeighbourhood()
    await neighbourhood.explore(SUBJECT.id, { depth: 1 })

    stubApi([
      { path: ROUTE.path, status: 404, body: { error: 'not_found', detail: 'Élément introuvable.' } },
    ])
    await neighbourhood.explore(SUBJECT.id, { depth: 1 })

    expect(neighbourhood.status.value).toBe('error')
    expect(neighbourhood.error.value).toBe('Élément introuvable.')
    expect(neighbourhood.graph.value.elements).toHaveLength(0)
  })

  it('reports an unreachable backend rather than an empty drawing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )
    const neighbourhood = useNeighbourhood()

    await neighbourhood.explore(SUBJECT.id, { depth: 1 })

    expect(neighbourhood.error.value).toMatch(/injoignable/i)
  })
})
