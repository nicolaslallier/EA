<script setup lang="ts">
// The `/analyse/couverture` section: is each requirement realized by a design,
// and tested by a test?
//
// There is nothing here the catalogue does not already hold: a requirement is
// an element and its realizers are links (docs/adr/0064). This screen is a
// reading of the server's coverage. The two filters live in the URL, per the
// rule in CLAUDE.md: a colleague should be able to be sent "the approved
// requirements nobody tests".
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { GAP_LABELS, PRIORITY_LABELS, STATUS_LABELS, labelOf } from './labels'
import { useCoverage } from './useCoverage'

const route = useRoute()
const router = useRouter()
const { coverage, error, load } = useCoverage()

/** A query parameter is `string | string[] | null`; only one value means anything. */
function one(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

const status = computed(() => one(route.query.status))
const priority = computed(() => one(route.query.priority))

watch(
  [status, priority],
  () => load({ status: status.value, priority: priority.value }),
  { immediate: true },
)

async function narrow(key: 'status' | 'priority', value: string): Promise<void> {
  // A dial turned replaces the entry: it is the same question, refined.
  await router.replace({ query: { ...route.query, [key]: value || undefined } })
}

function onStatus(event: Event): void {
  void narrow('status', (event.target as HTMLSelectElement).value)
}

function onPriority(event: Event): void {
  void narrow('priority', (event.target as HTMLSelectElement).value)
}

/** `null` is "nothing to measure", which is not 0 % and not 100 %. */
function percent(rate: number | null): string {
  return rate === null ? '—' : `${Math.round(rate * 100)} %`
}

const names = (items: { name: string }[]) => items.map((item) => item.name).join(', ') || '—'
</script>

<template>
  <section class="coverage" aria-labelledby="coverage-title">
    <header>
      <h2 id="coverage-title">Couverture des exigences</h2>
      <p>Chaque exigence est-elle réalisée par la conception, et vérifiée par un test ?</p>
    </header>

    <p v-if="error" role="alert" class="banner">{{ error }}</p>

    <form class="filters" @submit.prevent>
      <label>
        Statut
        <select :value="status" @change="onStatus">
          <option value="">Tous</option>
          <option v-for="key in Object.keys(coverage.by_status)" :key="key" :value="key">
            {{ labelOf(STATUS_LABELS, key) }} ({{ coverage.by_status[key] }})
          </option>
        </select>
      </label>
      <label>
        Priorité
        <select :value="priority" @change="onPriority">
          <option value="">Toutes</option>
          <option v-for="key in Object.keys(coverage.by_priority)" :key="key" :value="key">
            {{ labelOf(PRIORITY_LABELS, key) }} ({{ coverage.by_priority[key] }})
          </option>
        </select>
      </label>
    </form>

    <dl class="tiles">
      <div>
        <dt>Réalisées par la conception</dt>
        <dd>{{ coverage.designed }} sur {{ coverage.active }}</dd>
        <dd class="rate">{{ percent(coverage.design_rate) }}</dd>
      </div>
      <div>
        <dt>Vérifiées par un test</dt>
        <dd>{{ coverage.verified }} sur {{ coverage.active }}</dd>
        <dd class="rate">{{ percent(coverage.verification_rate) }}</dd>
      </div>
    </dl>

    <p v-if="coverage.total === 0" class="empty">Aucune exigence dans le catalogue.</p>
    <table v-else>
      <caption>
        Exigences, les plus importantes d'abord
      </caption>
      <thead>
        <tr>
          <th scope="col">Identifiant</th>
          <th scope="col">Exigence</th>
          <th scope="col">Statut</th>
          <th scope="col">Priorité</th>
          <th scope="col">Réalisée par</th>
          <th scope="col">Testée par</th>
          <th scope="col">Écart</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="trace in coverage.requirements" :key="trace.id" :data-gap="trace.gap">
          <td>{{ trace.identifier || '—' }}</td>
          <td>
            <RouterLink :to="{ name: 'elements', query: { element: trace.id } }">{{ trace.name }}</RouterLink>
          </td>
          <td>{{ labelOf(STATUS_LABELS, trace.status) }}</td>
          <td>{{ labelOf(PRIORITY_LABELS, trace.priority) }}</td>
          <td>{{ names(trace.designed_by) }}</td>
          <td>{{ names(trace.tested_by) }}</td>
          <td>{{ labelOf(GAP_LABELS, trace.gap) }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
.coverage {
  display: grid;
  gap: 1rem;
}
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
}
.tiles {
  display: flex;
  flex-wrap: wrap;
  gap: 1.5rem;
  margin: 0;
}
.tiles dd {
  margin: 0;
}
.rate {
  font-size: 1.5rem;
  font-weight: 600;
}
.banner {
  padding: 0.5rem 0.75rem;
  border: 1px solid currentcolor;
}
table {
  border-collapse: collapse;
  width: 100%;
}
th,
td {
  text-align: left;
  padding: 0.35rem 0.5rem;
  border-bottom: 1px solid var(--border, #8884);
}
</style>
