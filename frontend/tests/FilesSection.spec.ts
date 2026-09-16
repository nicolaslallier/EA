import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'
import { createMemoryHistory } from 'vue-router'

import FilesSection from '../src/features/files/FilesSection.vue'
import { useMe } from '../src/lib/me'
import { createAppRouter } from '../src/router'
import { aFile, aFileRecord, aListing, aStoredFile, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const TOP: Route = {
  path: '/files',
  body: aListing({ folders: ['inbox/'], files: [aStoredFile({ key: 'readme.md', name: 'readme.md' })] }),
}

async function open(query = '', routes: Route[] = [TOP]) {
  const calls = stubApi(routes)
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/fichiers${query}`)
  await router.isReady()
  render(FilesSection, { global: { plugins: [router] } })
  return { calls, router }
}

/**
 * Pick a file, the way a user does.
 *
 * `<input type="file">` is read-only from script, so the file list is defined
 * onto the element before the `change` event — as `DocumentPanel.spec.ts`
 * does; `fireEvent.update` does not set `files` on a file input in jsdom.
 */
async function pick(file: File): Promise<void> {
  const input = await screen.findByLabelText<HTMLInputElement>('Déposer des fichiers')
  Object.defineProperty(input, 'files', { value: [file], configurable: true })
  await fireEvent.change(input)
}

describe('FilesSection', () => {
  it('lists the folders, then the files, of the folder in the URL', async () => {
    const { calls } = await open('?prefix=inbox/', [
      {
        path: '/files',
        body: aListing({
          prefix: 'inbox/',
          folders: ['inbox/sub/'],
          files: [aStoredFile({ key: 'inbox/notes.md', name: 'notes.md' })],
        }),
      },
    ])

    expect(await screen.findByRole('button', { name: 'sub/' })).toBeTruthy()
    expect(screen.getByText('notes.md')).toBeTruthy()
    expect(calls[0]?.url.searchParams.get('prefix')).toBe('inbox/')
  })

  it('cuts a folder name with the listing\'s own prefix, not the URL\'s', async () => {
    // `?prefix=inbox` (no slash) still asks the backend for `inbox/`, and the
    // backend's answer carries that trailing slash in `prefix` — slicing by
    // the URL's four-character value would leave a leading `/` on the name.
    await open('?prefix=inbox', [
      {
        path: '/files',
        body: aListing({ prefix: 'inbox/', folders: ['inbox/sub/'], files: [] }),
      },
    ])

    expect(await screen.findByRole('button', { name: 'sub/' })).toBeTruthy()
  })

  it('opens a folder by writing it into the URL', async () => {
    const { router } = await open()

    await fireEvent.click(await screen.findByRole('button', { name: 'inbox/' }))

    await waitFor(() => expect(router.currentRoute.value.query.prefix).toBe('inbox/'))
  })

  it('asks before replacing a file that exists, then sends it again with overwrite', async () => {
    const { calls } = await open('', [
      TOP,
      { method: 'POST', path: '/files', status: 409, body: { error: 'duplicate', detail: 'exists' } },
    ])
    await pick(aFile('readme.md'))

    expect(await screen.findByText(/existe déjà/)).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: 'Remplacer' }))

    await waitFor(() => expect(calls.filter((call) => call.method === 'POST')).toHaveLength(2))
  })

  it('clears a stale failure banner once the clashes are replaced', async () => {
    await open('', [
      TOP,
      { method: 'POST', path: '/files', status: 409, body: { error: 'duplicate', detail: 'exists' } },
      { path: '/files/content', status: 500, body: { error: 'internal_error', detail: 'boom' } },
    ])
    await pick(aFile('readme.md'))
    expect(await screen.findByText(/existe déjà/)).toBeTruthy()
    await fireEvent.click(await screen.findByRole('button', { name: 'Télécharger readme.md' }))
    expect(await screen.findByText('boom')).toBeTruthy()

    await fireEvent.click(screen.getByRole('button', { name: 'Remplacer' }))

    await waitFor(() => expect(screen.queryByText('boom')).toBeNull())
  })

  it('reloads the folder even after a refused upload, so a partial batch is not left invisible', async () => {
    const { calls } = await open('', [
      TOP,
      {
        method: 'POST',
        path: '/files',
        status: 422,
        body: { error: 'file_too_large', detail: 'trop gros' },
      },
    ])

    await pick(aFile('big.bin'))

    expect(await screen.findByText(/trop gros/)).toBeTruthy()
    await waitFor(() =>
      expect(
        calls.filter((call) => call.method === 'GET' && call.url.pathname === '/files'),
      ).toHaveLength(2),
    )
  })

  it('offers no upload and no delete to a reader', async () => {
    vi.mocked(useMe).mockReturnValueOnce({
      me: ref({ username: 'reader', can_write: false }),
      canWrite: computed(() => false),
      error: ref(null),
      load: vi.fn(() => Promise.resolve()),
    })
    await open()

    await screen.findByText('readme.md')
    expect(screen.queryByLabelText('Déposer des fichiers')).toBeNull()
    expect(screen.queryByRole('button', { name: /Supprimer/ })).toBeNull()
    // Writing about a file and reconciling the catalogue are writes too
    // (docs/adr/0039); the API refuses them, and the screen does not offer them.
    expect(screen.queryByRole('button', { name: /^Décrire/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Rapprocher le catalogue' })).toBeNull()
  })

  it('downloads through the client, never through a bare link', async () => {
    const createObjectURL = vi.fn(() => 'blob:x')
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }))
    const { calls } = await open('', [TOP, { path: '/files/content', body: 'bytes' }])

    await fireEvent.click(await screen.findByRole('button', { name: 'Télécharger readme.md' }))

    await waitFor(() => expect(createObjectURL).toHaveBeenCalled())
    expect(calls.at(-1)?.url.searchParams.get('key')).toBe('readme.md')
  })
})

describe('what the catalogue says about each file', () => {
  it('shows the title, the tags and who deposited it', async () => {
    await open('', [
      {
        path: '/files',
        body: aListing({
          files: [
            aStoredFile({
              key: 'rapport.pdf',
              name: 'rapport.pdf',
              metadata: aFileRecord({
                title: 'Rapport 2026',
                description: 'Le bilan de l’année.',
                tags: ['budget'],
                uploaded_by: 'nicolas',
              }),
            }),
          ],
        }),
      },
    ])

    expect(await screen.findByText('Rapport 2026')).toBeTruthy()
    expect(screen.getByText('Le bilan de l’année.')).toBeTruthy()
    expect(screen.getByText('budget')).toBeTruthy()
    expect(screen.getByText(/Déposé par nicolas/)).toBeTruthy()
    // The path is still shown: the title names the file, it does not replace it.
    expect(screen.getByText('rapport.pdf')).toBeTruthy()
  })

  it('says so when a file has no record rather than pretending it has none to give', async () => {
    // The bucket has another door than this API — docs/adr/0039.
    await open('', [
      {
        path: '/files',
        body: aListing({
          files: [aStoredFile({ key: 'dropped.csv', name: 'dropped.csv', metadata: null })],
        }),
      },
    ])

    expect(await screen.findByText('Aucune fiche')).toBeTruthy()
  })

  it('replaces the three fields together and reloads the folder', async () => {
    const { calls } = await open('', [
      {
        path: '/files',
        body: aListing({
          files: [aStoredFile({ key: 'a.md', name: 'a.md', metadata: aFileRecord({ title: 'A' }) })],
        }),
      },
      { method: 'PUT', path: '/files/metadata', body: aStoredFile() },
    ])

    await fireEvent.click(await screen.findByRole('button', { name: 'Décrire a.md' }))
    await fireEvent.update(screen.getByLabelText('Titre'), 'B')
    await fireEvent.update(screen.getByLabelText('Étiquettes'), 'réseau, budget')
    await fireEvent.click(screen.getByRole('button', { name: 'Enregistrer' }))

    await waitFor(() => {
      const written = calls.find((call) => call.method === 'PUT')
      expect(written?.body).toEqual({ title: 'B', description: '', tags: ['réseau', 'budget'] })
    })
    // The listing is re-read, so what is on screen is what was stored.
    await waitFor(() =>
      expect(calls.filter((call) => call.url.pathname === '/files').length).toBeGreaterThan(1),
    )
  })

  it('reconciles the catalogue with the bucket and says what changed', async () => {
    const { calls } = await open('', [
      TOP,
      { method: 'POST', path: '/files/reconcile', body: { recorded: 2, forgotten: 1 } },
    ])

    await fireEvent.click(await screen.findByRole('button', { name: 'Rapprocher le catalogue' }))

    expect(await screen.findByText(/2 fichier\(s\) ajouté\(s\) au catalogue/)).toBeTruthy()
    expect(calls.some((call) => call.url.pathname === '/files/reconcile')).toBe(true)
  })
})
