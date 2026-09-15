import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'
import { createMemoryHistory } from 'vue-router'

import FilesSection from '../src/features/files/FilesSection.vue'
import { useMe } from '../src/lib/me'
import { createAppRouter } from '../src/router'
import { aFile, aListing, aStoredFile, stubApi, type Route } from './support/api'

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
