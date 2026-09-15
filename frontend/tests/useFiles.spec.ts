import { afterEach, describe, expect, it, vi } from 'vitest'

import { breadcrumbs, parentOf, uploadForm, useFiles } from '../src/features/files/useFiles'
import { MULTIPART, aFile, aListing, aStoredFile, stubApi } from './support/api'

afterEach(() => vi.unstubAllGlobals())

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
