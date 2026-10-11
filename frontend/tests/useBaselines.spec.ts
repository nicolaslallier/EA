import { afterEach, describe, expect, it, vi } from 'vitest'

import { useBaselines } from '../src/features/baselines/useBaselines'
import { aBaseline, aDiff, aRestore, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useBaselines', () => {
  it('lists, captures and removes', async () => {
    const calls = stubApi([
      { path: '/baselines', body: [aBaseline()] },
      {
        method: 'POST',
        path: '/baselines',
        status: 201,
        body: aBaseline({ name: 'v2' }),
      },
      { method: 'DELETE', path: `/baselines/${aBaseline().id}`, status: 204 },
    ])
    const baselines = useBaselines()

    await baselines.load()
    await baselines.capture('v2', 'après')
    await baselines.remove(aBaseline().id)

    expect(baselines.baselines.value).toHaveLength(1)
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({
      name: 'v2',
      description: 'après',
    })
    expect(calls.some((c) => c.method === 'DELETE')).toBe(true)
  })

  it('compares with the current model when no second baseline is named', async () => {
    const calls = stubApi([{ path: `/baselines/${aBaseline().id}/diff`, body: aDiff() }])
    const baselines = useBaselines()

    await baselines.compare(aBaseline().id)

    expect(calls[0]?.url.searchParams.has('against')).toBe(false)
    expect(baselines.diff.value).toEqual(aDiff())
  })

  it('compares two baselines under the name the backend reads', async () => {
    const calls = stubApi([{ path: `/baselines/${aBaseline().id}/diff`, body: aDiff() }])

    await useBaselines().compare(aBaseline().id, 'other-id')

    expect(calls[0]?.url.searchParams.get('against')).toBe('other-id')
  })

  it('keeps a failed diff apart from an empty one', async () => {
    stubApi([
      {
        path: `/baselines/${aBaseline().id}/diff`,
        status: 404,
        body: { error: 'not_found', detail: 'no baseline' },
      },
    ])
    const baselines = useBaselines()

    await baselines.compare(aBaseline().id)

    expect(baselines.diff.value).toBeNull()
    expect(baselines.diffStatus.value).toBe('error')
    expect(baselines.diffError.value).toBe('no baseline')
  })

  it('sends the restoration request as given and hands the answer back', async () => {
    const calls = stubApi([
      {
        method: 'POST',
        path: `/baselines/${aBaseline().id}/restore`,
        body: aRestore({ elements_to_delete: 3 }),
      },
    ])

    const answer = await useBaselines().restore(aBaseline().id, {
      dry_run: true,
    })

    expect(calls[0]?.body).toEqual({ dry_run: true })
    expect(answer.plan.elements_to_delete).toBe(3)
  })
})
