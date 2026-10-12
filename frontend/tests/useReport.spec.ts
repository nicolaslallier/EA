import { afterEach, describe, expect, it, vi } from 'vitest'

import { filenameOf, useReport } from '../src/features/reports/useReport'
import { stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const TEMPLATES = [
  { id: 'fiche-diagramme', version: 1, title: 'Fiche de diagramme', subjects: ['diagram'], formats: ['html', 'pdf'] },
  { id: 'inventaire-paquet', version: 1, title: "Inventaire d'un paquet", subjects: ['package'], formats: ['html'] },
]

describe('filenameOf', () => {
  it('reads the RFC 5987 name of an attachment', () => {
    expect(filenameOf("attachment; filename*=UTF-8''2026-10-10-vente-fiche.pdf", 'x')).toBe(
      '2026-10-10-vente-fiche.pdf',
    )
  })

  it('falls back when there is no header or it is malformed', () => {
    expect(filenameOf(null, 'repli.pdf')).toBe('repli.pdf')
    expect(filenameOf("attachment; filename*=UTF-8''%E0%A4%A", 'repli.pdf')).toBe('repli.pdf')
  })
})

describe('useReport', () => {
  it('keeps only the templates that take the subject', async () => {
    stubApi([{ path: '/report-templates', body: TEMPLATES }])
    const report = useReport()

    await report.load()

    expect(report.templatesFor('diagram').map((t) => t.id)).toEqual(['fiche-diagramme'])
    expect(report.templatesFor('package').map((t) => t.id)).toEqual(['inventaire-paquet'])
  })

  it('downloads through GET /reports/render with the subject in the query', async () => {
    const calls = stubApi([{ path: '/reports/render', body: { ok: true } }])
    const createObjectURL = vi.fn(() => 'blob:report')
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }))
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const report = useReport()

    await report.download('fiche-diagramme', 'diagram', 'dddddddd-0000-4000-8000-000000000000', 'html')

    const [call] = calls
    expect(call?.method).toBe('GET')
    expect(call?.url.searchParams.get('template')).toBe('fiche-diagramme')
    expect(call?.url.searchParams.get('subject_kind')).toBe('diagram')
    expect(call?.url.searchParams.get('subject_id')).toBe('dddddddd-0000-4000-8000-000000000000')
    expect(call?.url.searchParams.get('format')).toBe('html')
    expect(createObjectURL).toHaveBeenCalled()
    expect(click).toHaveBeenCalled()
  })

  it('deposits through POST /reports and keeps the answer', async () => {
    const calls = stubApi([{ method: 'POST', path: '/reports', status: 201, body: { key: 'reports/2026/x.html' } }])
    const report = useReport()

    await report.deposit('fiche-diagramme', 'diagram', 'dddddddd-0000-4000-8000-000000000000', 'html')

    expect(calls[0]?.body).toEqual({
      template: 'fiche-diagramme',
      subject: { kind: 'diagram', id: 'dddddddd-0000-4000-8000-000000000000' },
      format: 'html',
    })
    expect(report.deposited.value).toEqual({ key: 'reports/2026/x.html' })
  })

  it('puts a refusal in its state instead of throwing', async () => {
    stubApi([
      {
        method: 'POST',
        path: '/reports',
        status: 422,
        body: { error: 'report_too_large', detail: 'a report covers at most 500 elements' },
      },
    ])
    const report = useReport()

    await report.deposit('fiche-diagramme', 'diagram', 'dddddddd-0000-4000-8000-000000000000', 'html')

    expect(report.error.value).toContain('500 elements')
    expect(report.deposited.value).toBeNull()
  })
})
