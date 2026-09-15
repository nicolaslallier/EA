// The files of the bucket (docs/adr/0036): one folder at a time, and the writes.
//
// A folder is not stored anywhere — it is the shared start of some keys — so
// the screen navigates by prefix and the API computes what is below it. A
// download goes through the client rather than a link: the token lives in
// memory, and a plain `<a href>` would reach the API without it.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { ApiError, api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'

export type StoredFile = components['schemas']['FileRead']
export type FileListing = components['schemas']['FileListingRead']

/** The multipart body of `POST /files` — the one hand-written payload here. */
export function uploadForm(file: File, prefix: string, overwrite: boolean): FormData {
  const form = new FormData()
  form.append('file', file)
  form.append('prefix', prefix)
  form.append('overwrite', String(overwrite))
  return form
}

/** The folder above `prefix`: `a/b/` → `a/`, `a/` → the top. */
export function parentOf(prefix: string): string {
  const trimmed = prefix.replace(/\/$/, '')
  const cut = trimmed.lastIndexOf('/')
  return cut < 0 ? '' : trimmed.slice(0, cut + 1)
}

/** `a/b/` → the crumbs `a` and `b`, each with the prefix it opens. */
export function breadcrumbs(prefix: string): { label: string; prefix: string }[] {
  const parts = prefix.split('/').filter(Boolean)
  return parts.map((label, index) => ({ label, prefix: `${parts.slice(0, index + 1).join('/')}/` }))
}

/** Hand a downloaded blob to the browser under its own name. */
export function saveAs(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
}

export function useFiles() {
  const listing = ref<FileListing | null>(null)
  const request = useLatestRequest()
  const { status, error } = request

  async function load(prefix: string): Promise<void> {
    await request.run(
      async (signal) => unwrap(await api.GET('/files', { params: { query: { prefix } }, signal })),
      (answer) => {
        listing.value = answer
      },
    )
  }

  /** `'exists'` rather than a throw on a 409: replacing a file is a question for the user. */
  async function upload(prefix: string, file: File, overwrite = false): Promise<'stored' | 'exists'> {
    try {
      unwrap(
        await api.POST('/files', {
          // The generated type says `string` for a binary part; the value sent is
          // the `File`, serialised by `uploadForm` — see `useElementDocuments`.
          body: { file: file as unknown as string, prefix, overwrite },
          bodySerializer: () => uploadForm(file, prefix, overwrite),
        }),
      )
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 409) {
        return 'exists'
      }
      throw failure
    }
    return 'stored'
  }

  async function download(key: string): Promise<Blob> {
    return unwrap(await api.GET('/files/content', { params: { query: { key } }, parseAs: 'blob' }))
  }

  async function remove(key: string): Promise<void> {
    unwrap(await api.DELETE('/files', { params: { query: { key } } }))
  }

  return { listing, status, error, load, upload, download, remove }
}
