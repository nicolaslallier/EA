import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import DocumentPanel from '../src/features/documents/DocumentPanel.vue'
import { MULTIPART, aDocument, aDocumentSummary, aFile, anElement, stubApi } from './support/api'
import type { Route } from './support/api'

const ELEMENT = anElement({ name: 'Facturation' })
const DOCUMENT = 'dddddddd-1111-4111-8111-111111111111'
const LIST = `/elements/${ELEMENT.id}/documents`

function show(routes: Route[]) {
  const calls = stubApi(routes)
  render(DocumentPanel, { props: { element: ELEMENT } })
  return calls
}

/**
 * Pick a file, the way a user does.
 *
 * `<input type="file">` is read-only from script, so the file list is defined
 * onto the element before the `change` event — which is what the browser does
 * for real, and the only way to drive the control from a test.
 */
async function pick(file: File): Promise<void> {
  const input = screen.getByLabelText<HTMLInputElement>(/ajouter un fichier/i)
  Object.defineProperty(input, 'files', { value: [file], configurable: true })
  await fireEvent.change(input)
}

afterEach(() => vi.unstubAllGlobals())

describe('DocumentPanel', () => {
  it('lists the files attached to the element, with their size', async () => {
    show([{ path: LIST, body: [aDocumentSummary({ filename: 'runbook.md', byte_size: 2048 })] }])

    const panel = within(screen.getByRole('region', { name: /documents de l'élément/i }))
    expect(await panel.findByRole('button', { name: 'runbook.md' })).toBeInTheDocument()
    expect(panel.getByText('2 ko')).toBeInTheDocument()
  })

  it('says the element carries nothing rather than showing an empty table', async () => {
    show([{ path: LIST, body: [] }])

    expect(await screen.findByText(/aucun document attaché/i)).toBeInTheDocument()
  })

  it('says why when the documents cannot be read', async () => {
    show([
      {
        path: LIST,
        status: 404,
        body: { error: 'not_found', detail: "Cet élément n'existe pas" },
      },
    ])

    expect(await screen.findByRole('alert')).toHaveTextContent(/n'existe pas/)
  })

  it('uploads the file the user picks and shows it in the list', async () => {
    let listed: unknown[] = []
    const calls = show([
      { path: LIST, get body() { return listed } },
      { method: 'POST', path: LIST, status: 201, body: aDocument({ filename: 'notes.md' }) },
    ])
    await screen.findByText(/aucun document attaché/i)
    listed = [aDocumentSummary({ filename: 'notes.md' })]

    await pick(aFile('notes.md'))

    expect(await screen.findByRole('button', { name: 'notes.md' })).toBeInTheDocument()
    const upload = calls.find((call) => call.method === 'POST')
    expect(upload?.body).toBe(MULTIPART)
  })

  it('shows the API refusal instead of pretending the upload worked', async () => {
    show([
      { path: LIST, body: [] },
      {
        method: 'POST',
        path: LIST,
        status: 422,
        body: { error: 'invalid_input', detail: "'notes.txt' n'est pas un fichier markdown" },
      },
    ])
    await screen.findByText(/aucun document attaché/i)

    await pick(aFile('notes.txt'))

    expect(await screen.findByRole('alert')).toHaveTextContent(/markdown/)
  })

  it('shows the markdown of a document when its name is clicked', async () => {
    show([
      { path: LIST, body: [aDocumentSummary()] },
      { path: `/documents/${DOCUMENT}`, body: aDocument({ content: '# Runbook\n\nÉtape 1' }) },
    ])

    await fireEvent.click(await screen.findByRole('button', { name: 'runbook.md' }))

    const reader = within(await screen.findByRole('region', { name: /contenu du document/i }))
    expect(reader.getByText(/Étape 1/)).toBeInTheDocument()
  })

  it('asks before removing a document, and removes it once confirmed', async () => {
    let listed: unknown[] = [aDocumentSummary()]
    const calls = show([
      { path: LIST, get body() { return listed } },
      { method: 'DELETE', path: `/documents/${DOCUMENT}`, status: 204 },
    ])
    await screen.findByRole('button', { name: 'runbook.md' })

    await fireEvent.click(screen.getByRole('button', { name: /supprimer runbook\.md/i }))
    expect(screen.getByRole('alert')).toHaveTextContent(/supprimer ce document/i)
    listed = []
    await fireEvent.click(screen.getByRole('button', { name: /confirmer/i }))

    await waitFor(() => expect(screen.getByText(/aucun document attaché/i)).toBeInTheDocument())
    expect(calls.some((call) => call.method === 'DELETE')).toBe(true)
  })

  it('leaves the document alone when the confirmation is declined', async () => {
    const calls = show([{ path: LIST, body: [aDocumentSummary()] }])
    await screen.findByRole('button', { name: 'runbook.md' })

    await fireEvent.click(screen.getByRole('button', { name: /supprimer runbook\.md/i }))
    await fireEvent.click(screen.getByRole('button', { name: /renoncer/i }))

    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    expect(screen.getByRole('button', { name: 'runbook.md' })).toBeInTheDocument()
  })

  it('closes on demand', async () => {
    const calls = stubApi([{ path: LIST, body: [] }])
    const rendered = render(DocumentPanel, { props: { element: ELEMENT } })
    await screen.findByText(/aucun document attaché/i)

    await fireEvent.click(screen.getAllByRole('button', { name: /^fermer$/i })[0])

    expect(rendered.emitted().close).toBeTruthy()
    expect(calls).toHaveLength(1)
  })
})
