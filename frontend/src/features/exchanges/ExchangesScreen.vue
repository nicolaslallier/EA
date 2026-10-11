<script setup lang="ts">
// Échanger le modèle avec Sparx EA, Archi et les autres outils ArchiMate (docs/adr/0050).
//
// L'onglet, le format (et, plus loin, le périmètre) sont dans l'URL : ce sont des
// questions qu'on partage. Les fichiers choisis restent dans la mémoire du composant.
// L'API décide qui importe ; ici on cache seulement ce qu'un lecteur ne peut pas faire.
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { useMe } from '../../lib/me'
import ImportReportTable from './ImportReportTable.vue'
import {
  filesOf,
  useExchanges,
  type ConflictPolicy,
  type ExchangeFormat,
  type ImportChoice,
  type Picked,
  type ScopeKind,
} from './useExchanges'

const TABS = [
  ['export', 'Exporter'],
  ['import', 'Importer'],
] as const
type Tab = (typeof TABS)[number][0]
type Part = keyof Picked

const route = useRoute()
const router = useRouter()
const { canWrite } = useMe()
const exchanges = useExchanges()
const {
  report,
  skippedViews,
  digestProblem,
  exportStatus,
  exportError,
  simulateStatus,
  simulateError,
  confirmStatus,
  confirmError,
  warning,
  packages,
  queries,
  scopesError,
} = exchanges

const tab = computed<Tab>(() => (route.query.tab === 'import' ? 'import' : 'export'))
const format = computed<ExchangeFormat>(() => (route.query.format === 'csv' ? 'csv' : 'archimate-xml'))

const scope = computed<ScopeKind>(() =>
  route.query.scope === 'package' || route.query.scope === 'query' ? route.query.scope : 'model',
)
const packageId = computed(() => (typeof route.query.package === 'string' ? route.query.package : undefined))
const queryId = computed(() => (typeof route.query.query === 'string' ? route.query.query : undefined))
const targetPackage = ref('')
const scopeChosen = computed(
  () =>
    (scope.value !== 'package' || packageId.value !== undefined) &&
    (scope.value !== 'query' || queryId.value !== undefined),
)

const withViews = ref(true)
const safeCells = ref(true)
const picked = ref<Picked>({})
const conflict = ref<ConflictPolicy>('reuse')
const strict = ref(false)

const importChoice = computed<ImportChoice>(() => ({
  format: format.value,
  conflict: conflict.value,
  strict: strict.value,
  withViews: withViews.value,
  safeCells: safeCells.value,
  targetPackageId: targetPackage.value || undefined,
}))
const hasFiles = computed(() => filesOf(format.value, picked.value).length > 0)
const busy = computed(() => simulateStatus.value === 'loading' || confirmStatus.value === 'loading')
const canSimulate = computed(() => hasFiles.value && !busy.value)
const confirmable = computed(() => !busy.value && exchanges.canConfirm(importChoice.value))
const importError = computed(() => simulateError.value || confirmError.value)
const outcome = computed(() => {
  const current = report.value
  if (current === null) {
    return ''
  }
  if (current.dry_run) {
    return 'Simulation : rien n’a été écrit.'
  }
  return current.applied ? 'Import appliqué.' : 'Rien n’a été écrit.'
})

// The lists are only worth asking for when a screen shows them.
watch(
  [scope, tab, canWrite],
  () => {
    if (scope.value !== 'model' || (tab.value === 'import' && canWrite.value)) {
      void exchanges.loadScopes()
    }
  },
  { immediate: true },
)

function setScope(event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  setQuery({ scope: value === 'model' ? undefined : value, package: undefined, query: undefined })
}

function pickFrom(key: 'package' | 'query', event: Event): void {
  setQuery({ [key]: (event.target as HTMLSelectElement).value || undefined })
}

watch(format, () => {
  picked.value = {}
  exchanges.forget()
})

function setQuery(patch: Record<string, string | undefined>): void {
  void router.replace({ query: { ...route.query, ...patch } })
}

function openTab(next: Tab): void {
  void router.push({ query: { ...route.query, tab: next === 'export' ? undefined : next } })
}

function setFormat(event: Event): void {
  setQuery({ format: (event.target as HTMLSelectElement).value === 'csv' ? 'csv' : undefined })
}

async function onPick(part: Part, event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const next: Picked = { ...picked.value }
  next[part] = input.files?.[0]
  picked.value = next
  await exchanges.track(format.value, next)
}

function onExport(): void {
  void exchanges.exportModel({
    format: format.value,
    scope: scope.value,
    packageId: packageId.value,
    queryId: queryId.value,
    withViews: withViews.value,
    safeCells: safeCells.value,
  })
}

function onSimulate(): void {
  void exchanges.simulate(format.value, picked.value, importChoice.value)
}

function onConfirm(): void {
  void exchanges.confirm(format.value, picked.value, importChoice.value)
}
</script>

<template>
  <section class="exchanges">
    <h1>Échanges</h1>
    <p>Ouvrir et échanger le modèle avec Sparx EA, Archi et les autres outils ArchiMate.</p>

    <div role="tablist" aria-label="Sens de l'échange">
      <button
        v-for="[id, label] in TABS"
        :key="id"
        type="button"
        role="tab"
        :aria-selected="tab === id"
        @click="openTab(id)"
      >
        {{ label }}
      </button>
    </div>

    <form v-if="tab === 'export'" class="panel" @submit.prevent="onExport">
      <label>
        Format
        <select :value="format" @change="setFormat">
          <option value="archimate-xml">XML ArchiMate Exchange</option>
          <option value="csv">CSV (trois fichiers, dans un zip)</option>
        </select>
      </label>
      <label>
        Périmètre
        <select :value="scope" @change="setScope">
          <option value="model">Le modèle entier</option>
          <option value="package">Un paquet et ses sous-paquets</option>
          <option value="query">Une requête enregistrée</option>
        </select>
      </label>
      <label v-if="scope === 'package'">
        Paquet
        <select :value="packageId ?? ''" @change="pickFrom('package', $event)">
          <option value="">—</option>
          <option v-for="option in packages" :key="option.id" :value="option.id">{{ option.path }}</option>
        </select>
      </label>
      <label v-if="scope === 'query'">
        Requête
        <select :value="queryId ?? ''" @change="pickFrom('query', $event)">
          <option value="">—</option>
          <option v-for="option in queries" :key="option.id" :value="option.id">{{ option.name }}</option>
        </select>
      </label>
      <p v-if="scopesError && scope !== 'model'" class="hint">{{ scopesError }}</p>
      <label v-if="format === 'archimate-xml'">
        <input v-model="withViews" type="checkbox" />
        Inclure les vues
      </label>
      <label v-else>
        <input v-model="safeCells" type="checkbox" />
        Neutraliser les formules (=, +, -, @)
      </label>
      <button type="submit" :disabled="exportStatus === 'loading' || !scopeChosen">Télécharger</button>
      <p v-if="skippedViews > 0" role="status">
        {{ skippedViews }} vue(s) omise(s) : une de leurs boîtes est hors du périmètre exporté.
      </p>
      <p v-if="exportError" role="alert">{{ exportError }}</p>
    </form>

    <div v-else class="panel">
      <p v-if="!canWrite" role="note">
        L'import est réservé aux éditeurs : il écrit dans le catalogue, même pour une simulation.
      </p>
      <template v-else>
        <label>
          Format
          <select :value="format" @change="setFormat">
            <option value="archimate-xml">XML ArchiMate Exchange</option>
            <option value="csv">CSV (trois fichiers)</option>
          </select>
        </label>

        <template v-if="format === 'archimate-xml'">
          <label>
            Fichier d'échange (XML)
            <input type="file" accept=".xml,application/xml,text/xml" @change="onPick('file', $event)" />
          </label>
        </template>
        <template v-else>
          <label>
            elements.csv
            <input type="file" accept=".csv,text/csv" @change="onPick('elements', $event)" />
          </label>
          <label>
            relations.csv
            <input type="file" accept=".csv,text/csv" @change="onPick('relations', $event)" />
          </label>
          <label>
            properties.csv
            <input type="file" accept=".csv,text/csv" @change="onPick('properties', $event)" />
          </label>
        </template>

        <fieldset>
          <legend>Options</legend>
          <label>
            Un élément existant (même type et même nom)
            <select v-model="conflict">
              <option value="reuse">est réutilisé</option>
              <option value="fail">est une erreur</option>
            </select>
          </label>
          <label>
            <input v-model="strict" type="checkbox" />
            Tout ou rien (strict)
          </label>
          <label>
            Ranger les éléments créés dans
            <select v-model="targetPackage">
              <option value="">La racine</option>
              <option v-for="option in packages" :key="option.id" :value="option.id">{{ option.path }}</option>
            </select>
          </label>
          <label v-if="format === 'archimate-xml'">
            <input v-model="withViews" type="checkbox" />
            Importer les vues
          </label>
          <label v-else>
            <input v-model="safeCells" type="checkbox" />
            Restaurer les cellules neutralisées
          </label>
        </fieldset>

        <button type="button" :disabled="!canSimulate" @click="onSimulate">Simuler</button>
        <button type="button" :disabled="!confirmable" @click="onConfirm">Confirmer l'import</button>

        <p v-if="digestProblem" role="alert">{{ digestProblem }}</p>
        <p v-if="importError" role="alert">{{ importError }}</p>
        <p v-if="warning" role="status">{{ warning }}</p>

        <template v-if="report">
          <p role="status">{{ outcome }} Empreinte du fichier : {{ report.sha256.slice(0, 12) }}…</p>
          <ImportReportTable :report="report" />
          <RouterLink v-if="report.applied" :to="{ name: 'elements' }">Ouvrir le catalogue</RouterLink>
        </template>
      </template>
    </div>
  </section>
</template>

<style scoped>
.panel {
  display: grid;
  gap: 0.75rem;
  max-width: 60rem;
  margin-top: 1rem;
}
[role='tablist'] {
  display: flex;
  gap: 0.5rem;
}
[role='tab'][aria-selected='true'] {
  font-weight: 600;
}
</style>
