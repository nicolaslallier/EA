import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'
import { createMemoryHistory } from 'vue-router'

import ExchangesScreen from '../src/features/exchanges/ExchangesScreen.vue'
import { useMe } from '../src/lib/me'
import { createAppRouter } from '../src/router'
import { aPackage, aReport, anIssue, stubApi, type Route } from './support/api'

// The real digest needs `crypto.subtle`, which jsdom does not give; a name-and-size
// stand-in keeps the same property that matters: another file, another digest.
vi.mock('../src/features/exchanges/digest', () => ({
  sha256Of: (files: File[]) => Promise.resolve(files.map((f) => `${f.name}:${f.size}`).join('|')),
}))

afterEach(() => {
  vi.unstubAllGlobals()
})

const IMPORT: Route = {
  method: 'POST',
  path: '/exchanges/imports',
  body: aReport({ sha256: 'm.xml:1', issues: [anIssue({ code: 'relationship_not_allowed' })] }),
}

async function open(query = '', routes: Route[] = []) {
  const calls = stubApi(routes)
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/echanges${query}`)
  await router.isReady()
  render(ExchangesScreen, { global: { plugins: [router] } })
  return { calls, router }
}

async function pick(label: string, file: File): Promise<void> {
  const input = await screen.findByLabelText<HTMLInputElement>(label)
  Object.defineProperty(input, 'files', { value: [file], configurable: true })
  await fireEvent.change(input)
}

describe('the export tab', () => {
  it('downloads through the client, never through a bare link', async () => {
    const createObjectURL = vi.fn(() => 'blob:x')
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }))
    const { calls } = await open('', [{ path: '/exchanges/export', body: '<model/>' }])

    await fireEvent.click(await screen.findByRole('button', { name: 'Télécharger' }))

    await waitFor(() => expect(createObjectURL).toHaveBeenCalled())
    expect(calls[0]?.url.searchParams.get('format')).toBe('archimate-xml')
    expect(calls[0]?.url.searchParams.get('with_views')).toBe('true')
  })

  it('keeps the chosen format in the URL and asks for it', async () => {
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() }))
    const { calls, router } = await open('', [{ path: '/exchanges/export', body: 'zip' }])

    await fireEvent.update(await screen.findByLabelText('Format'), 'csv')
    // `router.replace` settles on a later tick: the URL is the state, so wait for it.
    await waitFor(() => expect(router.currentRoute.value.query.format).toBe('csv'))
    await fireEvent.click(screen.getByRole('button', { name: 'Télécharger' }))

    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0]?.url.searchParams.get('format')).toBe('csv')
  })

  it('tells how many views were left out of the file', async () => {
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() }))
    await open('', [{ path: '/exchanges/export', body: '<model/>', headers: { 'x-export-skipped-views': '2' } }])

    await fireEvent.click(await screen.findByRole('button', { name: 'Télécharger' }))

    expect(await screen.findByText(/2 vue\(s\) omise\(s\)/)).toBeTruthy()
  })
})

describe('the import tab', () => {
  it('is opened by the URL and offers the XML file', async () => {
    await open('?tab=import')

    expect(await screen.findByLabelText("Fichier d'échange (XML)")).toBeTruthy()
    expect(screen.getByRole('tab', { name: 'Importer', selected: true })).toBeTruthy()
  })

  it('cannot be confirmed before a simulation of the same file', async () => {
    await open('?tab=import', [IMPORT])
    const confirm = await screen.findByRole('button', { name: "Confirmer l'import" })
    expect(confirm).toBeDisabled()

    await pick("Fichier d'échange (XML)", new File(['x'], 'm.xml'))
    expect(confirm).toBeDisabled()

    await fireEvent.click(screen.getByRole('button', { name: 'Simuler' }))
    await screen.findByText('relationship_not_allowed')
    expect(confirm).toBeEnabled()
  })

  it('goes back to disabled when another file is chosen', async () => {
    await open('?tab=import', [IMPORT])
    await pick("Fichier d'échange (XML)", new File(['x'], 'm.xml'))
    await fireEvent.click(await screen.findByRole('button', { name: 'Simuler' }))
    await screen.findByText('relationship_not_allowed')

    await pick("Fichier d'échange (XML)", new File(['other'], 'n.xml'))

    expect(screen.getByRole('button', { name: "Confirmer l'import" })).toBeDisabled()
  })

  it('goes back to disabled when an option changes after the simulation', async () => {
    await open('?tab=import', [IMPORT])
    await pick("Fichier d'échange (XML)", new File(['x'], 'm.xml'))
    await fireEvent.click(await screen.findByRole('button', { name: 'Simuler' }))
    await screen.findByText('relationship_not_allowed')

    await fireEvent.click(screen.getByLabelText('Tout ou rien (strict)'))

    expect(screen.getByRole('button', { name: "Confirmer l'import" })).toBeDisabled()
  })

  it('shows the applied report with a link to the catalogue after confirming', async () => {
    await open('?tab=import', [IMPORT])
    await pick("Fichier d'échange (XML)", new File(['x'], 'm.xml'))
    await fireEvent.click(await screen.findByRole('button', { name: 'Simuler' }))
    await screen.findByText('relationship_not_allowed')
    stubApi([
      { method: 'POST', path: '/exchanges/imports', body: aReport({ dry_run: false, applied: true, sha256: 'm.xml:1' }) },
    ])

    await fireEvent.click(screen.getByRole('button', { name: "Confirmer l'import" }))

    expect(await screen.findByText(/Import appliqué/)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Ouvrir le catalogue' })).toBeTruthy()
  })

  it("shows the API's own words when it refuses the file", async () => {
    await open('?tab=import', [
      {
        method: 'POST',
        path: '/exchanges/imports',
        status: 413,
        body: { error: 'file_too_large', detail: 'an exchange file is at most 20971520 bytes' },
      },
    ])
    await pick("Fichier d'échange (XML)", new File(['x'], 'm.xml'))

    await fireEvent.click(await screen.findByRole('button', { name: 'Simuler' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('20971520')
  })

  it('takes the three CSV files in CSV mode', async () => {
    await open('?tab=import&format=csv')

    expect(await screen.findByLabelText('elements.csv')).toBeTruthy()
    expect(screen.getByLabelText('relations.csv')).toBeTruthy()
    expect(screen.getByLabelText('properties.csv')).toBeTruthy()
  })

  it('offers a reader neither the simulation nor the confirmation', async () => {
    vi.mocked(useMe).mockReturnValueOnce({
      me: ref({ username: 'reader', can_write: false, can_approve: false }),
      canWrite: computed(() => false),
      error: ref(null),
      load: vi.fn(() => Promise.resolve()),
    })
    await open('?tab=import')

    expect(await screen.findByText(/réservé aux éditeurs/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Simuler' })).toBeNull()
    expect(screen.queryByRole('button', { name: "Confirmer l'import" })).toBeNull()
  })

  it('lets a reader export', async () => {
    vi.mocked(useMe).mockReturnValueOnce({
      me: ref({ username: 'reader', can_write: false, can_approve: false }),
      canWrite: computed(() => false),
      error: ref(null),
      load: vi.fn(() => Promise.resolve()),
    })
    await open('')

    expect(await screen.findByRole('button', { name: 'Télécharger' })).toBeEnabled()
  })
})

describe('the scope', () => {
  it('puts the scope in the URL and asks the API for it', async () => {
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() }))
    const { calls, router } = await open('', [
      { path: '/exchanges/export', body: '<model/>' },
      { path: '/packages', body: [aPackage({ id: 'p1', name: 'Infra', path: 'Infra' })] },
      { path: '/queries', body: [] },
    ])

    await fireEvent.update(await screen.findByLabelText('Périmètre'), 'package')
    await screen.findByLabelText('Paquet')
    expect(screen.getByRole('button', { name: 'Télécharger' })).toBeDisabled()
    await screen.findByRole('option', { name: 'Infra' })
    await fireEvent.update(screen.getByLabelText('Paquet'), 'p1')
    await waitFor(() =>
      expect(router.currentRoute.value.query).toMatchObject({ scope: 'package', package: 'p1' }),
    )
    await fireEvent.click(screen.getByRole('button', { name: 'Télécharger' }))

    await waitFor(() => expect(calls.some((c) => c.url.pathname === '/exchanges/export')).toBe(true))
    const asked = calls.find((c) => c.url.pathname === '/exchanges/export')
    expect(asked?.url.searchParams.get('scope')).toBe('package')
    expect(asked?.url.searchParams.get('package_id')).toBe('p1')
  })
})
