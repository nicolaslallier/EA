import { afterEach, describe, expect, it, vi } from 'vitest'

import { useMetamodel } from '../src/features/metamodel/useMetamodel'
import { METAMODEL, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useMetamodel', () => {
  it('asks the backend for the palette rather than hard-coding 61 types', async () => {
    const calls = stubApi([{ path: '/metamodel', body: METAMODEL }])

    const metamodel = useMetamodel()
    await metamodel.load()

    expect(calls[0].url.pathname).toBe('/metamodel')
    expect(metamodel.elementTypes.value).toHaveLength(2)
    expect(metamodel.layers.value).toEqual(['business', 'application'])
  })

  it('groups the types by layer so a select can be read', async () => {
    stubApi([{ path: '/metamodel', body: METAMODEL }])

    const metamodel = useMetamodel()
    await metamodel.load()

    expect(metamodel.byLayer.value.map((group) => group.layer)).toEqual([
      'business',
      'application',
    ])
    expect(metamodel.byLayer.value[1].types.map((type) => type.value)).toEqual([
      'application_component',
    ])
  })

  it('reads the direction an outage travels from the metamodel, never from a table here', async () => {
    stubApi([{ path: '/metamodel', body: METAMODEL }])

    const metamodel = useMetamodel()
    await metamodel.load()

    // A service serves a process: the outage runs along the arrow. A whole is
    // composed of its parts: it runs back against it.
    expect(metamodel.followsArrow('serving')).toBe(true)
    expect(metamodel.followsArrow('composition')).toBe(false)
  })

  it('assumes an outage follows the arrow while the metamodel is still loading', () => {
    // The palette arrives after the traversal it explains; a cascade drawn in
    // the meantime must be redrawn, not left empty.
    expect(useMetamodel().followsArrow('serving')).toBe(true)
  })

  it('resolves a type to its human label, and falls back to the raw value', async () => {
    stubApi([{ path: '/metamodel', body: METAMODEL }])

    const metamodel = useMetamodel()
    await metamodel.load()

    expect(metamodel.labelOf('application_component')).toBe('Application Component')
    expect(metamodel.labelOf('junction')).toBe('junction')
  })

  it('leaves the palette empty rather than throwing when the backend is down', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )

    const metamodel = useMetamodel()
    await metamodel.load()

    expect(metamodel.elementTypes.value).toEqual([])
    expect(metamodel.error.value).toMatch(/injoignable/i)
  })
})
