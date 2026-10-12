// Generating a report: which templates fit a subject, the download, the deposit.
//
// A download is `GET /reports/render` (any reader, nothing stored); a deposit is
// `POST /reports` (editors, the API decides). Both go through `useLatestRequest`
// so a second click supersedes the first, and a refusal lands in `error` in
// words rather than being thrown at the dialog.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import { saveAs } from '../files/useFiles'

export type ReportTemplateRead = components['schemas']['ReportTemplateRead']
export type ReportFormat = ReportTemplateRead['formats'][number]
export type SubjectKind = ReportTemplateRead['subjects'][number]
type FileRead = components['schemas']['FileRead']

/** The name an `attachment; filename*=UTF-8''…` header carries, or `fallback`. */
export function filenameOf(header: string | null, fallback: string): string {
  const encoded = header?.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  if (!encoded) {
    return fallback
  }
  try {
    return decodeURIComponent(encoded)
  } catch {
    return fallback
  }
}

export function useReport() {
  const templates = ref<ReportTemplateRead[]>([])
  const deposited = ref<FileRead | null>(null)
  const catalogue = useLatestRequest()
  const work = useLatestRequest()

  async function load(): Promise<void> {
    await catalogue.run(
      async (signal) => unwrap(await api.GET('/report-templates', { signal })),
      (answer) => {
        templates.value = answer
      },
    )
  }

  function templatesFor(kind: SubjectKind): ReportTemplateRead[] {
    return templates.value.filter((template) => template.subjects.includes(kind))
  }

  async function download(
    template: string,
    kind: SubjectKind,
    id: string,
    format: ReportFormat,
  ): Promise<void> {
    await work.run(
      async (signal) => {
        const result = await api.GET('/reports/render', {
          params: { query: { template, subject_kind: kind, subject_id: id, format } },
          parseAs: 'blob',
          signal,
        })
        const blob = unwrap(result)
        const name = filenameOf(
          result.response.headers.get('content-disposition'),
          `${template}.${format}`,
        )
        return { blob, name }
      },
      ({ blob, name }) => {
        saveAs(blob, name)
      },
    )
  }

  async function deposit(
    template: string,
    kind: SubjectKind,
    id: string,
    format: ReportFormat,
  ): Promise<void> {
    deposited.value = null
    await work.run(
      async (signal) =>
        unwrap(
          await api.POST('/reports', {
            body: { template, subject: { kind, id }, format },
            signal,
          }),
        ),
      (file) => {
        deposited.value = file
      },
    )
  }

  return {
    templates,
    deposited,
    load,
    templatesFor,
    download,
    deposit,
    status: work.status,
    error: work.error,
    catalogueError: catalogue.error,
  }
}
