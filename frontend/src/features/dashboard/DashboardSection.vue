<script setup lang="ts">
// The `/analyse/tableau-de-bord` section: how the model is spread, and where it is
// worn — a count per layer, and a heat map crossing two dimensions.
//
// The question lives in the URL (`?by=`, `?cols=`, `?metric=`, `?property=`):
// a view of "layer × lifecycle" is a link one can send. Every number and every
// colour level comes from the API; this screen decides nothing.
import { computed, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { usePackages } from '../packages/usePackages'
import HeatmapChart from './HeatmapChart.vue'
import { keyLabel } from './presentation'
import { useDashboard, type DashboardQuestion, type Metric } from './useDashboard'

const route = useRoute()
const router = useRouter()
const dashboard = useDashboard()
const packages = usePackages()
/** Package ids as paths: the dimension is the server's, the names come from the tree. */
const names = computed(
  () => new Map(packages.packages.value.map((entry) => [entry.id, entry.path])),
)

function one(value: unknown, fallback: string): string {
  return typeof value === 'string' && value ? value : fallback
}

const question = computed<DashboardQuestion>(() => ({
  by: one(route.query.by, 'layer'),
  cols: one(route.query.cols, 'property:lifecycle'),
  // Passed on as it stands: the API knows the metrics and refuses any other.
  metric: one(route.query.metric, 'count') as Metric,
  property: one(route.query.property, ''),
}))

watch(
  question,
  async (asked) => {
    if (asked.by === 'package' || asked.cols === 'package') {
      void packages.load()
    }
    await dashboard.load(asked)
  },
  { immediate: true },
)

function ask(changes: Record<string, string | undefined>): void {
  void router.replace({ query: { ...route.query, ...changes } })
}

function onChange(name: string, event: Event): void {
  ask({ [name]: (event.target as HTMLSelectElement | HTMLInputElement).value || undefined })
}

const empty = computed(() => dashboard.tiles.value?.total === 0)
const unreadable = computed(() => dashboard.heatmap.value?.unreadable ?? 0)
</script>

<template>
  <section class="section">
    <header>
      <h2>Tableau de bord</h2>
      <p class="hint">Comment le modèle se répartit, et où il vieillit ou coûte.</p>
    </header>

    <form class="controls" aria-label="Question" @submit.prevent>
      <div class="field">
        <label for="dash-by">Lignes</label>
        <select id="dash-by" :value="question.by" @change="onChange('by', $event)">
          <option value="layer">Couche</option>
          <option value="element_type">Type d'élément</option>
          <option value="package">Paquet</option>
        </select>
      </div>
      <div class="field">
        <label for="dash-cols">Colonnes</label>
        <input id="dash-cols" :value="question.cols" placeholder="property:lifecycle"
               @change="onChange('cols', $event)" />
      </div>
      <div class="field">
        <label for="dash-metric">Mesure</label>
        <select id="dash-metric" :value="question.metric" @change="onChange('metric', $event)">
          <option value="count">Nombre</option>
          <option value="sum">Somme</option>
          <option value="avg">Moyenne</option>
          <option value="min">Minimum</option>
          <option value="max">Maximum</option>
        </select>
      </div>
      <div v-if="question.metric !== 'count'" class="field">
        <label for="dash-property">Propriété</label>
        <input id="dash-property" :value="question.property" placeholder="cost"
               @change="onChange('property', $event)" />
      </div>
    </form>

    <p v-if="dashboard.status.value === 'error'" class="banner banner--error" role="alert">
      {{ dashboard.error.value }}
    </p>
    <p v-else-if="dashboard.status.value === 'loading' && !dashboard.tiles.value" class="hint">
      Calcul en cours…
    </p>

    <template v-if="dashboard.tiles.value && dashboard.heatmap.value">
      <p v-if="empty" class="hint">Aucun élément à analyser.</p>
      <template v-else>
        <p v-if="unreadable > 0" class="banner" role="status">
          {{ unreadable }} valeurs illisibles ont été laissées de côté dans le calcul.
          <RouterLink :to="{ path: '/analyse/qualite', query: { code: 'unreadable_property' } }">
            Voir lesquelles
          </RouterLink>
        </p>

        <ul class="tiles" aria-label="Répartition">
          <li class="tile">
            <strong>{{ dashboard.tiles.value.total }} éléments</strong>
          </li>
          <li v-for="bucket in dashboard.tiles.value.buckets" :key="bucket.key" class="tile">
            <span>{{ keyLabel(bucket.key, names) }}</span>
            <strong>{{ bucket.count }}</strong>
          </li>
        </ul>

        <HeatmapChart :heatmap="dashboard.heatmap.value" :names="names" />
      </template>
    </template>
  </section>
</template>

<style scoped>
.tiles {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  list-style: none;
  padding: 0;
}
.tile {
  display: flex;
  flex-direction: column;
  min-width: 8rem;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--border, #c9c4d1);
  border-radius: 6px;
}
</style>
