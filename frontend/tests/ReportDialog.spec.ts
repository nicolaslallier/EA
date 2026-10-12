import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'
import { createMemoryHistory } from 'vue-router'

import ReportDialog from '../src/features/reports/ReportDialog.vue'
import { useMe } from '../src/lib/me'
import { createAppRouter } from '../src/router'
import { stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const ID = 'dddddddd-0000-4000-8000-000000000000'
const TEMPLATES = [
  { id: 'fiche-diagramme', version: 1, title: 'Fiche de diagramme', subjects: ['diagram'], formats: ['html', 'pdf'] },
  { id: 'inventaire-paquet', version: 1, title: "Inventaire d'un paquet", subjects: ['package'], formats: ['html'] },
]

async function open(props: Record<string, unknown> = {}) {
  const router = createAppRouter(createMemoryHistory())
  await router.push('/diagrammes')
  const view = render(ReportDialog, {
    props: { subjectKind: 'diagram', subjectId: ID, ...props },
    global: { plugins: [router] },
  })
  await fireEvent.click(screen.getByRole('button', { name: /Générer un rapport/ }))
  return { router, view }
}

describe('ReportDialog', () => {
  it('offers only the templates that take this kind of subject', async () => {
    stubApi([{ path: '/report-templates', body: TEMPLATES }])

    await open()

    expect(await screen.findByRole('option', { name: 'Fiche de diagramme' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: "Inventaire d'un paquet" })).toBeNull()
  })

  it('downloads for everybody, and an editor may also deposit', async () => {
    stubApi([{ path: '/report-templates', body: TEMPLATES }])

    await open()

    expect(await screen.findByRole('button', { name: 'Télécharger' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Déposer dans les fichiers' })).toBeInTheDocument()
  })

  it('hides the deposit from a reader and never calls POST /reports', async () => {
    const calls = stubApi([{ path: '/report-templates', body: TEMPLATES }])
    vi.mocked(useMe).mockReturnValueOnce({
      me: ref({ username: 'lecteur', can_write: false, can_approve: false }),
      canWrite: computed(() => false),
      error: ref(null),
      load: vi.fn(() => Promise.resolve()),
    })

    await open()

    await screen.findByRole('button', { name: 'Télécharger' })
    expect(screen.queryByRole('button', { name: 'Déposer dans les fichiers' })).toBeNull()
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('hides the deposit in download-only mode, even for an editor', async () => {
    stubApi([{ path: '/report-templates', body: TEMPLATES }])

    await open({ downloadOnly: true })

    await screen.findByRole('button', { name: 'Télécharger' })
    expect(screen.queryByRole('button', { name: 'Déposer dans les fichiers' })).toBeNull()
  })

  it('deposits, then lands on the reports folder of the file browser', async () => {
    const calls = stubApi([
      { path: '/report-templates', body: TEMPLATES },
      { method: 'POST', path: '/reports', status: 201, body: { key: 'reports/2026/x.html' } },
    ])
    const { router } = await open()

    await fireEvent.click(await screen.findByRole('button', { name: 'Déposer dans les fichiers' }))

    await waitFor(() => expect(router.currentRoute.value.path).toBe('/fichiers'))
    expect(router.currentRoute.value.query.prefix).toBe('reports/')
    expect(calls.some((call) => call.method === 'POST' && call.url.pathname === '/reports')).toBe(true)
  })

  it('says why a report was refused', async () => {
    stubApi([
      { path: '/report-templates', body: TEMPLATES },
      {
        method: 'POST',
        path: '/reports',
        status: 422,
        body: { error: 'report_too_large', detail: 'a report covers at most 500 elements' },
      },
    ])
    await open()

    await fireEvent.click(await screen.findByRole('button', { name: 'Déposer dans les fichiers' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('500 elements')
  })
})
