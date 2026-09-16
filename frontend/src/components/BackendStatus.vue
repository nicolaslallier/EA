<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchHealth, type Health } from '../lib/health'

type State = { kind: 'loading' } | { kind: 'ready'; health: Health } | { kind: 'error' }

// Labels, not rules — the sections a degraded deployment refuses are named by
// the API (docs/adr/0037); this only says them in French. A name with no entry
// is shown as it came, because a section nobody translated yet is still a
// section that is down.
const SECTION_LABELS: Record<string, string> = {
  files: 'fichiers',
  search: 'recherche documentaire',
}

const state = ref<State>({ kind: 'loading' })

const degraded = computed(() =>
  state.value.kind === 'ready'
    ? state.value.health.degraded.map((section) => SECTION_LABELS[section] ?? section)
    : [],
)

onMounted(async () => {
  try {
    state.value = { kind: 'ready', health: await fetchHealth() }
  } catch {
    state.value = { kind: 'error' }
  }
})
</script>

<template>
  <p v-if="state.kind === 'loading'" class="status status--loading">Vérification du backend…</p>
  <p v-else-if="state.kind === 'error'" class="status status--error">
    Backend injoignable — as-tu lancé <code>make run-be</code> ?
  </p>
  <p v-else-if="degraded.length > 0" class="status status--degraded">
    Backend: {{ state.health.status }} — indisponible pour l'instant : {{ degraded.join(', ') }}.
    Le reste du catalogue répond normalement.
  </p>
  <p v-else class="status status--ok">Backend: {{ state.health.status }}</p>
</template>

<style scoped>
.status {
  font: inherit;
  padding: 0.6rem 0.9rem;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.status--ok {
  border-color: #2e7d32;
  color: #2e7d32;
}
.status--degraded {
  border-color: #ef6c00;
  color: #ef6c00;
}
.status--error {
  border-color: #c62828;
  color: #c62828;
}
</style>
