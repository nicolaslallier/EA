// The markdown attached to one element: the listing, and the four writes.
//
// A listing carries no content on purpose — the API answers file names and
// sizes (`DocumentSummaryRead`), and the text of one document is a second call
// (`GET /documents/{id}`). Loading ten bodies of a megabyte each to draw ten
// file names is exactly what the two shapes prevent, so this module keeps them
// apart rather than merging them into one type with an optional field.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'

export type DocumentSummary = components['schemas']['DocumentSummaryRead']
export type DocumentRead = components['schemas']['DocumentRead']

/**
 * The `multipart/form-data` body the two upload endpoints take.
 *
 * Exported because it is the one hand-written piece of a payload in this
 * feature — everything else comes from the generated schema — and because it
 * is worth asserting on directly: the part is named `file`, which is what the
 * backend reads, and it carries the name the user picked.
 */
export function markdownForm(file: File): FormData {
  const form = new FormData()
  form.append('file', file)
  return form
}

/**
 * Send one picked file as `multipart/form-data`.
 *
 * The generated types describe a binary part as `string`, which is what
 * OpenAPI says a `format: binary` field is; the value actually sent is a
 * `File`. The cast is that mismatch, named here once so no call site has to
 * know about it — and `bodySerializer` is what hands the client the form
 * instead of JSON. Nothing here describes the payload: the path and the
 * response types still come from the generated schema.
 */
function filePart(file: File) {
  return {
    body: { file: file as unknown as string },
    bodySerializer: () => markdownForm(file),
  }
}

export function useElementDocuments() {
  const items = ref<DocumentSummary[]>([])
  const listing = useLatestRequest()
  const { status, error } = listing
  /** The document whose markdown is on screen, or nothing. */
  const opened = ref<DocumentRead | null>(null)
  // Two names clicked in a row are two reads; the text shown must be the
  // second file's, and a megabyte still downloading for the first is dropped.
  const reading = useLatestRequest()
  /** Why the document last clicked could not be read; '' when it could. */
  const readError = reading.error

  async function load(elementId: string): Promise<void> {
    await listing.run(
      async (signal) =>
        unwrap(
          await api.GET('/elements/{element_id}/documents', {
            params: { path: { element_id: elementId } },
            signal,
          }),
        ),
      (answer) => {
        items.value = answer
      },
    )
  }

  /**
   * Attach a file, or replace the document that already carries its name.
   *
   * The API refuses a second document under a name an element already has
   * (409), which is the right answer to a blind upload but a poor one to a
   * user re-picking the file they just edited. The listing is already here, so
   * the case is recognised before the request rather than after the refusal.
   */
  async function upload(elementId: string, file: File): Promise<void> {
    const existing = items.value.find((item) => item.filename === file.name)
    if (existing) {
      unwrap(
        await api.PUT('/documents/{document_id}', {
          params: { path: { document_id: existing.id } },
          ...filePart(file),
        }),
      )
    } else {
      unwrap(
        await api.POST('/elements/{element_id}/documents', {
          params: { path: { element_id: elementId } },
          ...filePart(file),
        }),
      )
    }
    await load(elementId)
  }

  /** Read the markdown of one document, which the listing never carries. */
  async function open(documentId: string): Promise<void> {
    await reading.run(
      async (signal) =>
        unwrap(
          await api.GET('/documents/{document_id}', {
            params: { path: { document_id: documentId } },
            signal,
          }),
        ),
      (answer) => {
        opened.value = answer
      },
      () => {
        opened.value = null
      },
    )
  }

  function close(): void {
    reading.cancel()
    opened.value = null
  }

  async function remove(elementId: string, documentId: string): Promise<void> {
    unwrap(
      await api.DELETE('/documents/{document_id}', {
        params: { path: { document_id: documentId } },
      }),
    )
    if (opened.value?.id === documentId) {
      opened.value = null
    }
    await load(elementId)
  }

  return { items, status, error, opened, readError, load, upload, open, close, remove }
}
