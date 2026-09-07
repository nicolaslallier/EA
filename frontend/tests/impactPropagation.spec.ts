import { describe, expect, it } from 'vitest'

import { propagate } from '../src/features/impact/propagation'
import { aGraph, anElement, aRelationship } from './support/api'

const API = anElement({ id: 'aaaaaaaa-1111-4111-8111-111111111111', name: 'API de facturation' })
const PROCESS = anElement({
  id: 'bbbbbbbb-2222-4222-8222-222222222222',
  name: 'Order to cash',
  element_type: 'business_process',
  layer: 'business',
})
const WHOLE = anElement({ id: 'cccccccc-3333-4333-8333-333333333333', name: 'Chaîne de vente' })
const DASHBOARD = anElement({ id: 'dddddddd-4444-4444-8444-444444444444', name: 'Tableau de bord' })

/**
 * What `/metamodel` answers about direction — `impact_follows_direction` — and
 * not a rule restated here: the test states the fact the API would serve.
 */
const follows = (type: string) => type !== 'composition' && type !== 'access'

function link(type: string, source: string, target: string, id = `${type}:${source}>${target}`) {
  return aRelationship({ id, relationship_type: type as never, source_id: source, target_id: target })
}

describe('propagate', () => {
  it('follows the arrow where the metamodel says an outage does', () => {
    // The API serves the process, so losing the API breaks the process.
    const graph = aGraph([API, PROCESS], [link('serving', API.id, PROCESS.id)])

    const { hops } = propagate(graph, API.id, follows)

    expect(hops.get(API.id)).toBe(0)
    expect(hops.get(PROCESS.id)).toBe(1)
  })

  it('refuses to run backwards along an arrow it must follow forwards', () => {
    // Same link, other subject: the process failing does not break the API.
    const graph = aGraph([API, PROCESS], [link('serving', API.id, PROCESS.id)])

    const { hops, waves } = propagate(graph, PROCESS.id, follows)

    expect(hops.has(API.id)).toBe(false)
    expect(waves).toEqual([])
  })

  it('runs against the arrow where the metamodel says an outage does', () => {
    // A whole is composed *of* its parts: losing the part breaks the whole.
    const graph = aGraph([WHOLE, API], [link('composition', WHOLE.id, API.id)])

    expect(propagate(graph, API.id, follows).hops.get(WHOLE.id)).toBe(1)
  })

  it('crosses both kinds of hop in one cascade', () => {
    const graph = aGraph(
      [API, PROCESS, WHOLE],
      [link('serving', API.id, PROCESS.id), link('composition', WHOLE.id, PROCESS.id)],
    )

    const { hops } = propagate(graph, API.id, follows)

    expect(hops.get(PROCESS.id)).toBe(1)
    expect(hops.get(WHOLE.id)).toBe(2)
  })

  it('keeps the shortest cascade when two lead to the same element', () => {
    const graph = aGraph(
      [API, PROCESS, DASHBOARD],
      [
        link('serving', API.id, PROCESS.id),
        link('serving', PROCESS.id, DASHBOARD.id),
        link('serving', API.id, DASHBOARD.id, 'shortcut'),
      ],
    )

    expect(propagate(graph, API.id, follows).hops.get(DASHBOARD.id)).toBe(1)
  })

  it('gathers the impacted elements in waves, nearest first, without the subject', () => {
    const graph = aGraph(
      [API, PROCESS, WHOLE],
      [link('serving', API.id, PROCESS.id), link('composition', WHOLE.id, PROCESS.id)],
    )

    const { waves } = propagate(graph, API.id, follows)

    expect(waves).toEqual([
      { hops: 1, elements: [PROCESS] },
      { hops: 2, elements: [WHOLE] },
    ])
  })

  it('orders a wave by name, because a list is read and not drawn', () => {
    const zulu = anElement({ id: 'eeeeeeee-5555-4555-8555-555555555555', name: 'Zulu' })
    const alpha = anElement({ id: 'ffffffff-6666-4666-8666-666666666666', name: 'Alpha' })
    const graph = aGraph(
      [API, zulu, alpha],
      [link('serving', API.id, zulu.id), link('serving', API.id, alpha.id)],
    )

    expect(propagate(graph, API.id, follows).waves[0].elements.map((e) => e.name)).toEqual([
      'Alpha',
      'Zulu',
    ])
  })

  it('calls inert the links that explain no distance', () => {
    // Both are impacted at one hop; the link between them explains neither, so
    // the drawing must not present it as a step of the cascade.
    const graph = aGraph(
      [API, PROCESS, DASHBOARD],
      [
        link('serving', API.id, PROCESS.id, 'step-a'),
        link('serving', API.id, DASHBOARD.id, 'step-b'),
        link('association', PROCESS.id, DASHBOARD.id, 'sideways'),
      ],
    )

    expect(propagate(graph, API.id, follows).inert).toEqual(new Set(['sideways']))
  })

  it('says nothing at all about a subject the response does not contain', () => {
    const { hops, waves } = propagate(aGraph([API], []), PROCESS.id, follows)

    expect(hops.size).toBe(0)
    expect(waves).toEqual([])
  })
})
