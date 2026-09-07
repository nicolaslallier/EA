<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchHealth } from '../lib/health'

type State = { kind: 'loading' } | { kind: 'ready'; status: string } | { kind: 'error' }

const state = ref<State>({ kind: 'loading' })

onMounted(async () => {
  try {
    state.value = { kind: 'ready', status: await fetchHealth() }
  } catch {
    state.value = { kind: 'error' }
  }
})
</script>

<template>
  <p v-if="state.kind === 'loading'" class="status status--loading">Vérification du backend…</p>
  <p v-else-if="state.kind === 'ready'" class="status status--ok">Backend: {{ state.status }}</p>
  <p v-else class="status status--error">Backend injoignable — as-tu lancé <code>make run-be</code> ?</p>
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
.status--error {
  border-color: #c62828;
  color: #c62828;
}
</style>
