import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  markdownForm,
  useElementDocuments,
} from '../src/features/documents/useElementDocuments'
import { MULTIPART, aDocument, aDocumentSummary, aFile, deferApi, stubApi } from './support/api'

const ELEMENT = '11111111-1111-4111-8111-111111111111'
const DOCUMENT = 'dddddddd-1111-4111-8111-111111111111'

afterEach(() => vi.unstubAllGlobals())

describe('markdownForm', () => {
  // The one hand-written payload of this feature. The backend reads a part
  // named `file` and takes the document's name from it, so both are asserted
  // here rather than through a request the test environment cannot serialise.
  it('sends the picked file under the part name the backend reads', () => {
    const file = aFile('notes.md')

    expect(markdownForm(file).get('file')).toBe(file)
  })
})

describe('useElementDocuments', () => {
  it('lists what is attached to one element', async () => {
    stubApi([{ path: `/elements/${ELEMENT}/documents`, body: [aDocumentSummary()] }])
    const documents = useElementDocuments()

    await documents.load(ELEMENT)

    expect(documents.status.value).toBe('ready')
    expect(documents.items.value.map((item) => item.filename)).toEqual(['runbook.md'])
  })

  it('says why when the listing cannot be read', async () => {
    stubApi([
      {
        path: `/elements/${ELEMENT}/documents`,
        status: 404,
        body: { error: 'not_found', detail: "Cet élément n'existe pas" },
      },
    ])
    const documents = useElementDocuments()

    await documents.load(ELEMENT)

    expect(documents.status.value).toBe('error')
    expect(documents.error.value).toBe("Cet élément n'existe pas")
  })

  it('uploads a new file as multipart, under the name the user picked', async () => {
    const calls = stubApi([
      { path: `/elements/${ELEMENT}/documents`, body: [] },
      { method: 'POST', path: `/elements/${ELEMENT}/documents`, status: 201, body: aDocument() },
    ])
    const documents = useElementDocuments()
    await documents.load(ELEMENT)

    await documents.upload(ELEMENT, aFile('notes.md'))

    const upload = calls.find((call) => call.method === 'POST')
    expect(upload?.url.pathname).toBe(`/elements/${ELEMENT}/documents`)
    expect(upload?.body).toBe(MULTIPART)
  })

  it('reloads the listing after an upload, so the new file shows up', async () => {
    const calls = stubApi([
      { path: `/elements/${ELEMENT}/documents`, body: [] },
      { method: 'POST', path: `/elements/${ELEMENT}/documents`, status: 201, body: aDocument() },
    ])
    const documents = useElementDocuments()
    await documents.load(ELEMENT)

    await documents.upload(ELEMENT, aFile())

    expect(calls.filter((call) => call.method === 'GET')).toHaveLength(2)
  })

  it('replaces the document that already carries the picked name', async () => {
    // The API answers a duplicate name with a 409, which is right for a blind
    // upload and wrong for a user re-picking the file they just edited. The
    // listing is already loaded, so the case is recognised before the request.
    const calls = stubApi([
      { path: `/elements/${ELEMENT}/documents`, body: [aDocumentSummary()] },
      { method: 'PUT', path: `/documents/${DOCUMENT}`, body: aDocument() },
    ])
    const documents = useElementDocuments()
    await documents.load(ELEMENT)

    await documents.upload(ELEMENT, aFile('runbook.md'))

    expect(calls.map((call) => call.method)).toContain('PUT')
    expect(calls.some((call) => call.method === 'POST')).toBe(false)
  })

  it('lets the caller show the failure when the API refuses a file', async () => {
    stubApi([
      { path: `/elements/${ELEMENT}/documents`, body: [] },
      {
        method: 'POST',
        path: `/elements/${ELEMENT}/documents`,
        status: 422,
        body: { error: 'invalid_input', detail: "'notes.txt' n'est pas du markdown" },
      },
    ])
    const documents = useElementDocuments()
    await documents.load(ELEMENT)

    await expect(documents.upload(ELEMENT, aFile('notes.txt'))).rejects.toThrow(
      /markdown/,
    )
  })

  it('reads the markdown of one document, which the listing never carries', async () => {
    stubApi([{ path: `/documents/${DOCUMENT}`, body: aDocument({ content: '# Titre\n' }) }])
    const documents = useElementDocuments()

    await documents.open(DOCUMENT)

    expect(documents.opened.value?.content).toBe('# Titre\n')
  })

  it('shows the document clicked last, whichever answer arrives first', async () => {
    const other = 'eeeeeeee-2222-4222-8222-222222222222'
    const calls = deferApi()
    const documents = useElementDocuments()

    const first = documents.open(DOCUMENT)
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    const second = documents.open(other)
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    calls[1].answer(aDocument({ id: other, filename: 'notes.md' }))
    calls[0].answer(aDocument())
    await Promise.all([first, second])

    expect(documents.opened.value?.filename).toBe('notes.md')
    expect(documents.readError.value).toBe('')
  })

  it('says why a document could not be read, and forgets it once another opens', async () => {
    stubApi([
      { path: `/documents/${DOCUMENT}`, status: 404, body: { error: 'not_found', detail: 'Parti.' } },
    ])
    const documents = useElementDocuments()
    await documents.open(DOCUMENT)
    expect(documents.readError.value).toBe('Parti.')
    expect(documents.opened.value).toBeNull()

    stubApi([{ path: `/documents/${DOCUMENT}`, body: aDocument() }])
    await documents.open(DOCUMENT)

    expect(documents.readError.value).toBe('')
    expect(documents.opened.value?.filename).toBe('runbook.md')
  })

  it('closes the document it was showing', async () => {
    stubApi([{ path: `/documents/${DOCUMENT}`, body: aDocument() }])
    const documents = useElementDocuments()
    await documents.open(DOCUMENT)

    documents.close()

    expect(documents.opened.value).toBeNull()
  })

  it('removes a document and stops showing it', async () => {
    const calls = stubApi([
      { path: `/documents/${DOCUMENT}`, body: aDocument() },
      { method: 'DELETE', path: `/documents/${DOCUMENT}`, status: 204 },
      { path: `/elements/${ELEMENT}/documents`, body: [] },
    ])
    const documents = useElementDocuments()
    await documents.open(DOCUMENT)

    await documents.remove(ELEMENT, DOCUMENT)

    expect(calls.some((call) => call.method === 'DELETE')).toBe(true)
    expect(documents.opened.value).toBeNull()
    expect(documents.items.value).toEqual([])
  })
})
