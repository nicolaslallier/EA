import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  breadcrumbs,
  detailsOf,
  noDetails,
  parentOf,
  saveAs,
  tagsOf,
  uploadForm,
  useFiles,
} from '../src/features/files/useFiles'
import { MULTIPART, aFile, aFileRecord, aListing, aStoredFile, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('the pure helpers', () => {
  it('sends the file, its folder and the overwrite flag under the names the backend reads', () => {
    const file = aFile('notes.md')
    const form = uploadForm(file, 'inbox/', true)

    expect(form.get('file')).toBe(file)
    expect(form.get('prefix')).toBe('inbox/')
    expect(form.get('overwrite')).toBe('true')
  })

  it('finds the folder above', () => {
    expect(parentOf('a/b/')).toBe('a/')
    expect(parentOf('a/')).toBe('')
    expect(parentOf('')).toBe('')
  })

  it('cuts a folder into the crumbs that lead to it', () => {
    expect(breadcrumbs('a/b/')).toEqual([
      { label: 'a', prefix: 'a/' },
      { label: 'b', prefix: 'a/b/' },
    ])
    expect(breadcrumbs('')).toEqual([])
  })

  it('revokes the object URL only after the click has had a chance to fire', () => {
    // Revoking synchronously would sometimes invalidate the URL before the
    // browser has actually followed the download link — see docs/adr/0036.
    vi.useFakeTimers()
    const revokeObjectURL = vi.fn()
    const createObjectURL = vi.fn(() => 'blob:x')
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL }))

    saveAs(new Blob(['x']), 'notes.md')

    expect(revokeObjectURL).not.toHaveBeenCalled()
    vi.runAllTimers()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:x')
  })
})

describe('useFiles', () => {
  it('lists the folder it is asked for', async () => {
    const calls = stubApi([
      { path: '/files', body: aListing({ prefix: 'inbox/', files: [aStoredFile()] }) },
    ])
    const files = useFiles()

    await files.load('inbox/')

    expect(calls[0]?.url.searchParams.get('prefix')).toBe('inbox/')
    expect(files.listing.value?.files.map((file) => file.name)).toEqual(['notes.md'])
  })

  it('uploads as multipart and says it was stored', async () => {
    const calls = stubApi([{ method: 'POST', path: '/files', status: 201, body: aStoredFile() }])

    expect(await useFiles().upload('inbox/', aFile('notes.md'))).toBe('stored')
    expect(calls[0]?.body).toBe(MULTIPART)
  })

  it('answers "exists" on a 409 instead of throwing, so the screen can ask', async () => {
    stubApi([
      { method: 'POST', path: '/files', status: 409, body: { error: 'duplicate', detail: 'exists' } },
    ])

    expect(await useFiles().upload('', aFile('notes.md'))).toBe('exists')
  })

  it('still throws any other refusal', async () => {
    stubApi([
      {
        method: 'POST',
        path: '/files',
        status: 422,
        body: { error: 'file_too_large', detail: 'trop gros' },
      },
    ])

    await expect(useFiles().upload('', aFile('big.bin'))).rejects.toThrow('trop gros')
  })

  it('deletes by key', async () => {
    const calls = stubApi([{ method: 'DELETE', path: '/files', status: 204 }])

    await useFiles().remove('inbox/notes.md')

    expect(calls[0]?.url.searchParams.get('key')).toBe('inbox/notes.md')
  })
})

describe('what a person writes about a file', () => {
  it('sends the title, the description and one form field per tag', () => {
    const form = uploadForm(aFile('rapport.pdf'), 'inbox/', false, {
      title: 'Rapport 2026',
      description: 'Le bilan.',
      tags: ['budget', 'réseau'],
    })

    expect(form.get('title')).toBe('Rapport 2026')
    expect(form.get('description')).toBe('Le bilan.')
    // A multipart body has no arrays — one field per tag, which is what
    // FastAPI reads back into a list.
    expect(form.getAll('tags')).toEqual(['budget', 'réseau'])
  })

  it('cuts a comma-separated line into tags, leaving the case to the server', () => {
    // The API lowercases and de-duplicates (docs/adr/0039); doing it here too
    // would be a second answer to what a tag is.
    expect(tagsOf(' Réseau , budget ,, ')).toEqual(['Réseau', 'budget'])
    expect(tagsOf('')).toEqual([])
  })

  it('starts the form from what the file already carries', () => {
    const file = aStoredFile({ metadata: aFileRecord({ title: 'A', tags: ['budget'] }) })

    expect(detailsOf(file)).toEqual({ title: 'A', description: '', tags: ['budget'] })
  })

  it('starts the form empty for a file the catalogue has never seen', () => {
    expect(detailsOf(aStoredFile({ metadata: null }))).toEqual(noDetails())
  })

  it('replaces the three fields together, which is what the endpoint does', async () => {
    const calls = stubApi([
      { method: 'PUT', path: '/files/metadata', body: aStoredFile() },
    ])

    await useFiles().describe('inbox/notes.md', {
      title: 'B',
      description: '',
      tags: ['réseau'],
    })

    expect(calls[0]?.url.searchParams.get('key')).toBe('inbox/notes.md')
    expect(calls[0]?.body).toEqual({ title: 'B', description: '', tags: ['réseau'] })
  })

  it('asks the server to make the catalogue agree with the bucket', async () => {
    stubApi([
      { method: 'POST', path: '/files/reconcile', body: { recorded: 2, forgotten: 1 } },
    ])

    expect(await useFiles().reconcile()).toEqual({ recorded: 2, forgotten: 1 })
  })
})
