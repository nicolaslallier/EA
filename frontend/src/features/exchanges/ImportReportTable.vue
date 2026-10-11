<script setup lang="ts">
// Le rapport d'un import : des compteurs toujours complets, des lignes éventuellement
// tronquées par le serveur. Les messages viennent d'un fichier étranger : on les rend
// comme du texte (jamais `v-html`).
import { computed, ref } from 'vue'

import type { ImportReport } from './useExchanges'

type Issue = ImportReport['issues'][number]

const props = defineProps<{ report: ImportReport }>()

const CATEGORIES = [
  ['elements', 'Éléments'],
  ['relationships', 'Relations'],
  ['packages', 'Paquets'],
  ['views', 'Vues'],
] as const
const OUTCOMES = [
  ['created', 'Créés'],
  ['reused', 'Réutilisés'],
  ['rejected', 'Rejetés'],
  ['ignored', 'Ignorés'],
] as const
const SEVERITIES: Record<Issue['severity'], string> = {
  error: 'Erreur',
  warning: 'Avertissement',
  info: 'Information',
}

const severity = ref<'all' | Issue['severity']>('all')
const search = ref('')
const sortByCode = ref(false)

const lines = computed(() => {
  const wanted = search.value.trim().toLowerCase()
  const kept = props.report.issues.filter(
    (issue) =>
      (severity.value === 'all' || issue.severity === severity.value) &&
      (wanted === '' || (issue.source_id ?? '').toLowerCase().includes(wanted)),
  )
  return sortByCode.value ? [...kept].sort((a, b) => a.code.localeCompare(b.code)) : kept
})

function where(issue: Issue): string {
  return issue.line != null ? `ligne ${issue.line}` : (issue.path ?? '')
}
</script>

<template>
  <section class="report" aria-label="Rapport d'import">
    <table class="counts">
      <thead>
        <tr>
          <th scope="col">Catégorie</th>
          <th v-for="[key, label] in OUTCOMES" :key="key" scope="col">{{ label }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="[category, label] in CATEGORIES" :key="category">
          <th scope="row">{{ label }}</th>
          <td v-for="[key] in OUTCOMES" :key="key">{{ report.counts[category][key] }}</td>
        </tr>
      </tbody>
    </table>

    <p v-if="report.truncated" role="status">
      Rapport tronqué : seules les premières lignes sont affichées, les compteurs sont complets.
    </p>

    <div class="filters">
      <label>
        Gravité
        <select v-model="severity">
          <option value="all">Toutes</option>
          <option v-for="(label, key) in SEVERITIES" :key="key" :value="key">{{ label }}</option>
        </select>
      </label>
      <label>
        Identifiant
        <input v-model="search" type="search" />
      </label>
      <button type="button" @click="sortByCode = !sortByCode">Trier par code</button>
    </div>

    <p v-if="lines.length === 0">Aucune ligne à afficher.</p>
    <table v-else class="issues">
      <thead>
        <tr>
          <th scope="col">Gravité</th>
          <th scope="col">Code</th>
          <th scope="col">Identifiant</th>
          <th scope="col">Où</th>
          <th scope="col">Message</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(issue, index) in lines" :key="index">
          <td>{{ SEVERITIES[issue.severity] }}</td>
          <td data-testid="issue-code">{{ issue.code }}</td>
          <td>{{ issue.source_id ?? '' }}</td>
          <td>{{ where(issue) }}</td>
          <td>{{ issue.message }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
table {
  border-collapse: collapse;
  width: 100%;
}
th,
td {
  border: 1px solid var(--border, #ccc);
  padding: 0.25rem 0.5rem;
  text-align: left;
}
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  margin: 1rem 0;
}
</style>
