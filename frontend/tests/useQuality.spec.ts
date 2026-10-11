import { afterEach, describe, expect, it, vi } from 'vitest'

import { useQuality } from '../src/features/quality/useQuality'
import { aFinding, aQualityReport, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const NO_FILTER = { severity: '', code: '' } as const

describe('useQuality', () => {
  it('leaves an unset filter out of the query', async () => {
    const calls = stubApi([{ path: '/quality', body: aQualityReport() }])
    const quality = useQuality()

    await quality.load(NO_FILTER)

    expect(calls[0].url.searchParams.has('severity')).toBe(false)
    expect(calls[0].url.searchParams.has('code')).toBe(false)
    expect(quality.findings.value).toHaveLength(1)
  })

  it('sends the filters it was given', async () => {
    const calls = stubApi([{ path: '/quality', body: aQualityReport([]) }])
    const quality = useQuality()

    await quality.load({ severity: 'error', code: 'orphan' })

    expect(calls[0].url.searchParams.get('severity')).toBe('error')
    expect(calls[0].url.searchParams.get('code')).toBe('orphan')
  })

  it('appends the next page from the cursor', async () => {
    const first = aFinding({ element_name: 'A' })
    const second = aFinding({ element_name: 'B' })
    const calls = stubApi([{ path: '/quality', body: aQualityReport([first], { next_cursor: 1 }) }])
    const quality = useQuality()
    await quality.load(NO_FILTER)

    stubApi([{ path: '/quality', body: aQualityReport([second]) }])
    await quality.more(NO_FILTER)

    expect(calls).toHaveLength(1)
    expect(quality.findings.value.map((finding) => finding.element_name)).toEqual(['A', 'B'])
    expect(quality.report.value?.next_cursor).toBeNull()
  })

  it('reports a refusal in words', async () => {
    stubApi([{ path: '/quality', status: 422, body: { error: 'scope_too_large', detail: 'trop large' } }])
    const quality = useQuality()

    await quality.load(NO_FILTER)

    expect(quality.error.value).toBe('trop large')
    expect(quality.report.value).toBeNull()
  })
})
