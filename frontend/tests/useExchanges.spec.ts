import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  filenameOf,
  filesOf,
  importForm,
  useExchanges,
  type ImportChoice,
} from '../src/features/exchanges/useExchanges'
import { aReport, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const choice: ImportChoice = {
  format: 'archimate-xml',
  conflict: 'reuse',
  strict: false,
  withViews: true,
  safeCells: true,
}
const file = (name: string, content = 'x'): File => new File([content], name)
const digest = vi.fn((files: File[]) => Promise.resolve(files.map((f) => `${f.name}:${f.size}`).join('|')))

describe('the pure helpers', () => {
  it('lists the files in the order the server digests them', () => {
    const [e, r, p] = [file('e.csv'), file('r.csv'), file('p.csv')]

    expect(filesOf('csv', { properties: p, elements: e, relations: r })).toEqual([e, r, p])
    expect(filesOf('csv', { elements: e })).toEqual([e])
    expect(filesOf('archimate-xml', { file: e, elements: r })).toEqual([e])
  })

  it('builds the multipart body of a simulation by default', () => {
    const form = importForm('archimate-xml', { file: file('m.xml') }, { ...choice, strict: true }, true)

    expect(form.get('dry_run')).toBe('true')
    expect(form.get('strict')).toBe('true')
    expect(form.get('format')).toBe('archimate-xml')
    expect((form.get('file') as File).name).toBe('m.xml')
  })

  it('sends the three CSV parts under their own names and only the ones chosen', () => {
    const form = importForm('csv', { elements: file('e.csv'), relations: file('r.csv') }, choice, false)

    expect(form.get('dry_run')).toBe('false')
    expect([...form.keys()]).toEqual(expect.arrayContaining(['elements', 'relations']))
    expect(form.has('properties')).toBe(false)
    expect(form.has('file')).toBe(false)
  })

  it('reads the server-made file name, with a fallback', () => {
    expect(filenameOf("attachment; filename*=UTF-8''ea-model-2026-10-10.xml", 'x')).toBe(
      'ea-model-2026-10-10.xml',
    )
    expect(filenameOf(null, 'ea-export.xml')).toBe('ea-export.xml')
  })
})

describe('simulating, then confirming', () => {
  it('can be confirmed only for the very files that were simulated, with the same options', async () => {
    const picked = { file: file('a.xml') }
    stubApi([{ method: 'POST', path: '/exchanges/imports', body: aReport({ sha256: 'a.xml:1' }) }])
    const exchanges = useExchanges({ digest })

    await exchanges.track('archimate-xml', picked)
    expect(exchanges.canConfirm(choice)).toBe(false)

    await exchanges.simulate('archimate-xml', picked, choice)
    expect(exchanges.report.value?.dry_run).toBe(true)
    expect(exchanges.canConfirm(choice)).toBe(true)

    expect(exchanges.canConfirm({ ...choice, strict: true })).toBe(false)
    await exchanges.track('archimate-xml', { file: file('b.xml', 'yy') })
    expect(exchanges.canConfirm(choice)).toBe(false)
    await exchanges.track('archimate-xml', picked)
    expect(exchanges.canConfirm(choice)).toBe(true)
  })

  it('replaces the simulation by the applied report, and cannot confirm twice', async () => {
    const picked = { file: file('a.xml') }
    stubApi([{ method: 'POST', path: '/exchanges/imports', body: aReport({ sha256: 'a.xml:1' }) }])
    const exchanges = useExchanges({ digest })
    await exchanges.track('archimate-xml', picked)
    await exchanges.simulate('archimate-xml', picked, choice)

    stubApi([
      {
        method: 'POST',
        path: '/exchanges/imports',
        body: aReport({ dry_run: false, applied: true, sha256: 'a.xml:1' }),
      },
    ])
    await exchanges.confirm('archimate-xml', picked, choice)

    expect(exchanges.report.value?.applied).toBe(true)
    expect(exchanges.canConfirm(choice)).toBe(false)
  })

  it('reports a refusal in words and keeps the simulation', async () => {
    const picked = { file: file('a.xml') }
    stubApi([
      {
        method: 'POST',
        path: '/exchanges/imports',
        status: 413,
        body: { error: 'file_too_large', detail: 'at most 20971520 bytes' },
      },
    ])
    const exchanges = useExchanges({ digest })

    await exchanges.simulate('archimate-xml', picked, choice)

    expect(exchanges.simulateError.value).toBe('at most 20971520 bytes')
    expect(exchanges.report.value).toBeNull()
  })

  it('says why when the browser cannot compute the digest', async () => {
    const exchanges = useExchanges({ digest: () => Promise.reject(new Error('no subtle')) })

    await exchanges.track('archimate-xml', { file: file('a.xml') })

    expect(exchanges.digestProblem.value).toMatch(/empreinte/)
    expect(exchanges.canConfirm(choice)).toBe(false)
  })
})

describe('exporting', () => {
  it('asks for the format and the scope, then hands the blob to the browser under its own name', async () => {
    const createObjectURL = vi.fn(() => 'blob:x')
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }))
    const calls = stubApi([
      {
        path: '/exchanges/export',
        body: '<model/>',
        headers: {
          'content-disposition': "attachment; filename*=UTF-8''ea-model-2026-10-10.xml",
          'x-export-skipped-views': '2',
        },
      },
    ])
    const exchanges = useExchanges({ digest })

    await exchanges.exportModel({ format: 'csv', scope: 'model', withViews: false, safeCells: true })

    expect(calls[0]?.url.searchParams.get('format')).toBe('csv')
    expect(calls[0]?.url.searchParams.get('scope')).toBe('model')
    expect(calls[0]?.url.searchParams.get('with_views')).toBe('false')
    expect(createObjectURL).toHaveBeenCalled()
    expect(exchanges.skippedViews.value).toBe(2)
  })
})

describe('the typed refusals, in words', () => {
  async function refused(status: number, error: string, detail: string, headers = {}) {
    stubApi([{ method: 'POST', path: '/exchanges/imports', status, body: { error, detail }, headers }])
    const exchanges = useExchanges({ digest })
    await exchanges.simulate('archimate-xml', { file: file('a.xml') }, choice)
    return exchanges.simulateError.value
  }

  it('says when to try again after a 429, from Retry-After', async () => {
    stubApi([
      {
        path: '/exchanges/export',
        status: 429,
        body: { error: 'export_in_progress', detail: 'too many exports' },
        headers: { 'retry-after': '5' },
      },
    ])
    const exchanges = useExchanges({ digest })

    await exchanges.exportModel({ format: 'csv', scope: 'model', withViews: false, safeCells: true })

    expect(exchanges.exportError.value).toMatch(/too many exports.*5 s/)
  })

  it('names the 408, the 409 and the 422 by what the API says', async () => {
    expect(await refused(408, 'body_timeout', 'the file was too slow')).toBe('the file was too slow')
    expect(await refused(409, 'write_conflict', 'another write is running')).toMatch(
      /another write is running.*relancer/,
    )
    expect(await refused(409, 'import_in_progress', 'an import is running')).toMatch(/an import is running/)
    expect(await refused(422, 'invalid_exchange', 'not an exchange file')).toBe('not an exchange file')
  })
})
