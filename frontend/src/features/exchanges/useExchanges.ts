// Les échanges (docs/adr/0050) : télécharger le modèle, ou en importer un fichier en
// deux temps — simuler, puis confirmer le *même* fichier avec les *mêmes* options.
//
// L'API décide de tout ; ici on garde en mémoire ce qui a été simulé pour ne
// proposer la confirmation que sur la même chose. Les fichiers ne quittent jamais
// la mémoire du composant (ni `localStorage`).
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { ApiError, api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import { saveAs } from '../files/useFiles'
import { sha256Of } from './digest'

export type ImportReport = components['schemas']['ImportReportRead']
export type ExchangeFormat = components['schemas']['ExchangeFormat']
// L'API type `scope` en `string` : la liste est ici, et c'est elle qui décide (422 sinon).
export type ScopeKind = 'model' | 'package' | 'query'
export type PackageOption = components['schemas']['PackageRead']
export type QueryOption = components['schemas']['SavedQuerySummaryRead']
export type ConflictPolicy = components['schemas']['ConflictPolicy']

export type ExportChoice = {
  format: ExchangeFormat
  scope: ScopeKind
  packageId?: string
  queryId?: string
  withViews: boolean
  safeCells: boolean
}

export type ImportChoice = {
  format: ExchangeFormat
  conflict: ConflictPolicy
  strict: boolean
  withViews: boolean
  safeCells: boolean
  targetPackageId?: string
}

export type Picked = { file?: File; elements?: File; relations?: File; properties?: File }

const NO_DIGEST =
  "Ce navigateur ne peut pas calculer l'empreinte du fichier (contexte non sécurisé ?) : l'import ne peut pas être confirmé."

/**
 * `unwrap`, avec ce que l'API dit en en-tête et que le message seul ne porte pas.
 *
 * 429 : `Retry-After` dit quand revenir. 409 `write_conflict` : une autre écriture a gagné
 * la course ; l'import est idempotent, le relancer est sans danger.
 */
function unwrapExchange<T>(result: { data?: T; error?: unknown; response: Response }): T {
  try {
    return unwrap(result)
  } catch (caught) {
    if (!(caught instanceof ApiError)) {
      throw caught
    }
    if (caught.status === 429) {
      const wait = result.response.headers.get('retry-after')
      throw new ApiError(
        wait ? `${caught.message} Réessayez dans ${wait} s.` : caught.message,
        caught.code,
        caught.status,
      )
    }
    if (caught.code === 'write_conflict') {
      throw new ApiError(
        `${caught.message} Rien n'a été écrit : vous pouvez relancer l'import.`,
        caught.code,
        caught.status,
      )
    }
    throw caught
  }
}

/** Les fichiers dans l'ordre où le serveur les digère : le XML seul, ou elements, relations, properties. */
export function filesOf(format: ExchangeFormat, picked: Picked): File[] {
  const order = format === 'csv' ? (['elements', 'relations', 'properties'] as const) : (['file'] as const)
  return order.flatMap((part) => {
    const chosen = picked[part]
    return chosen ? [chosen] : []
  })
}

export function importForm(
  format: ExchangeFormat,
  picked: Picked,
  choice: ImportChoice,
  dryRun: boolean,
): FormData {
  const form = new FormData()
  form.append('format', format)
  for (const part of format === 'csv' ? (['elements', 'relations', 'properties'] as const) : (['file'] as const)) {
    const chosen = picked[part]
    if (chosen) {
      form.append(part, chosen)
    }
  }
  form.append('dry_run', String(dryRun))
  form.append('on_conflict', choice.conflict)
  form.append('strict', String(choice.strict))
  form.append('with_views', String(choice.withViews))
  form.append('safe_cells', String(choice.safeCells))
  if (choice.targetPackageId) {
    form.append('target_package_id', choice.targetPackageId)
  }
  return form
}

/** `attachment; filename*=UTF-8''ea-model-….xml` → le nom, ou `fallback`. */
export function filenameOf(disposition: string | null, fallback: string): string {
  const found = /filename\*=UTF-8''([^;]+)/i.exec(disposition ?? '')
  if (!found?.[1]) {
    return fallback
  }
  try {
    return decodeURIComponent(found[1])
  } catch {
    return fallback
  }
}

export function useExchanges(deps: { digest?: (files: File[]) => Promise<string> } = {}) {
  const digest = deps.digest ?? sha256Of
  const report = ref<ImportReport | null>(null)
  const skippedViews = ref(0)
  const digestProblem = ref('')
  /** L'empreinte de ce que l'utilisateur a choisi *maintenant*. */
  const currentDigest = ref<string | null>(null)
  /** Les options de la simulation en cours de validité. */
  const simulatedWith = ref('')

  /** Ce qui cloche après coup : l'import appliqué n'est pas celui qui a été simulé. */
  const warning = ref('')
  const packages = ref<PackageOption[]>([])
  const queries = ref<QueryOption[]>([])

  const scopes = useLatestRequest()
  const exporting = useLatestRequest()
  const simulation = useLatestRequest()
  const confirmation = useLatestRequest()
  const tracking = useLatestRequest()

  async function track(format: ExchangeFormat, picked: Picked): Promise<void> {
    const files = filesOf(format, picked)
    await tracking.run(
      async () => {
        if (files.length === 0) {
          return { value: null, problem: '' }
        }
        try {
          return { value: await digest(files), problem: '' }
        } catch {
          return { value: null, problem: NO_DIGEST }
        }
      },
      ({ value, problem }) => {
        currentDigest.value = value
        digestProblem.value = problem
      },
    )
  }

  async function simulate(format: ExchangeFormat, picked: Picked, choice: ImportChoice): Promise<void> {
    await simulation.run(
      async (signal) =>
        unwrapExchange(
          await api.POST('/exchanges/imports', {
            body: {},
            bodySerializer: () => importForm(format, picked, choice, true),
            signal,
          }),
        ),
      (answer) => {
        report.value = answer
        simulatedWith.value = JSON.stringify(choice)
      },
    )
  }

  async function confirm(format: ExchangeFormat, picked: Picked, choice: ImportChoice): Promise<void> {
    await confirmation.run(
      async (signal) =>
        unwrapExchange(
          await api.POST('/exchanges/imports', {
            body: {},
            bodySerializer: () => importForm(format, picked, choice, false),
            signal,
          }),
        ),
      (answer) => {
        // Le serveur redonne l'empreinte de ce qu'il vient d'écrire : si elle diffère de
        // celle qu'on avait simulée, on le dit — l'écriture, elle, est déjà faite.
        warning.value =
          answer.sha256 === report.value?.sha256
            ? ''
            : "L'empreinte du fichier appliqué diffère de celle de la simulation."
        report.value = answer
        simulatedWith.value = ''
      },
    )
  }

  /** Une simulation réussie, du même fichier (empreinte), avec les mêmes options. */
  function canConfirm(choice: ImportChoice): boolean {
    const simulated = report.value
    return (
      simulated !== null &&
      simulated.dry_run &&
      currentDigest.value !== null &&
      simulated.sha256 === currentDigest.value &&
      simulatedWith.value === JSON.stringify(choice)
    )
  }

  async function exportModel(choice: ExportChoice): Promise<void> {
    await exporting.run(
      async (signal) => {
        const result = await api.GET('/exchanges/export', {
          params: {
            query: {
              format: choice.format,
              scope: choice.scope,
              package_id: choice.packageId,
              query_id: choice.queryId,
              with_views: choice.withViews,
              safe_cells: choice.safeCells,
            },
          },
          parseAs: 'blob',
          signal,
        })
        const extension = choice.format === 'csv' ? 'zip' : 'xml'
        return {
          blob: unwrapExchange(result),
          name: filenameOf(result.response.headers.get('content-disposition'), `ea-export.${extension}`),
          skipped: Number(result.response.headers.get('x-export-skipped-views') ?? 0),
        }
      },
      ({ blob, name, skipped }) => {
        saveAs(blob, name)
        skippedViews.value = skipped
      },
    )
  }

  /** Les paquets et les requêtes enregistrées, pour choisir un périmètre. */
  async function loadScopes(): Promise<void> {
    await scopes.run(
      async (signal) => ({
        packages: unwrap(await api.GET('/packages', { signal })),
        queries: unwrap(await api.GET('/queries', { signal })),
      }),
      (answer) => {
        packages.value = answer.packages
        queries.value = answer.queries
      },
    )
  }

  /** Oublier la simulation — par exemple quand le format change. */
  function forget(): void {
    report.value = null
    warning.value = ''
    simulatedWith.value = ''
    currentDigest.value = null
    digestProblem.value = ''
    simulation.cancel()
    confirmation.cancel()
  }

  return {
    report,
    warning,
    packages,
    queries,
    loadScopes,
    scopesError: scopes.error,
    skippedViews,
    digestProblem,
    exportStatus: exporting.status,
    exportError: exporting.error,
    simulateStatus: simulation.status,
    simulateError: simulation.error,
    confirmStatus: confirmation.status,
    confirmError: confirmation.error,
    track,
    simulate,
    confirm,
    canConfirm,
    exportModel,
    forget,
  }
}
