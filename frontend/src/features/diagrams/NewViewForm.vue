<script setup lang="ts">
// "Nouvelle vue": a diagram that is everything a saved query or a package holds.
// It decides nothing — it names the source and whether to freeze at once.
import { computed, ref } from 'vue'

import type { components } from '../../api/schema'

type ViewSource = components['schemas']['ViewSource']

defineProps<{
  queries: { id: string; name: string }[]
  packages: { id: string; path: string }[]
}>()
const emit = defineEmits<{ create: [payload: { name: string; source: ViewSource; frozen: boolean }] }>()

const name = ref('')
const kind = ref<'query' | 'package'>('query')
const queryId = ref('')
const packageId = ref('')
const recursive = ref(false)
const frozen = ref(false)

const ready = computed(
  () => name.value.trim() !== '' && (kind.value === 'query' ? queryId.value !== '' : packageId.value !== ''),
)

function submit(): void {
  const source: ViewSource =
    kind.value === 'query'
      ? { query_id: queryId.value, package_id: null, recursive: false }
      : { query_id: null, package_id: packageId.value, recursive: recursive.value }
  emit('create', { name: name.value.trim(), source, frozen: frozen.value })
}
</script>

<template>
  <form class="new-view" aria-label="Nouvelle vue" @submit.prevent="submit">
    <label for="view-name">Nom de la vue</label>
    <input id="view-name" v-model="name" maxlength="200" autocomplete="off" />

    <fieldset>
      <legend>Source</legend>
      <label><input v-model="kind" type="radio" value="query" /> Une requête</label>
      <label><input v-model="kind" type="radio" value="package" /> Un paquet</label>
    </fieldset>

    <template v-if="kind === 'query'">
      <label for="view-query">Requête enregistrée</label>
      <select id="view-query" v-model="queryId">
        <option value="" disabled>Choisir…</option>
        <option v-for="query in queries" :key="query.id" :value="query.id">{{ query.name }}</option>
      </select>
    </template>
    <template v-else>
      <label for="view-package">Paquet</label>
      <select id="view-package" v-model="packageId">
        <option value="" disabled>Choisir…</option>
        <option v-for="pack in packages" :key="pack.id" :value="pack.id">{{ pack.path }}</option>
      </select>
      <label><input v-model="recursive" type="checkbox" /> Avec les sous-paquets</label>
    </template>

    <fieldset>
      <legend>Comportement</legend>
      <label><input v-model="frozen" type="radio" :value="false" /> Vivante</label>
      <label><input v-model="frozen" type="radio" :value="true" /> Figée</label>
    </fieldset>
    <p class="hint">
      Vivante : recalculée à chaque ouverture, en lecture seule. Figée : la mise en page est
      enregistrée et le diagramme s'édite comme un autre.
    </p>

    <button type="submit" :disabled="!ready">Créer la vue</button>
  </form>
</template>

<style scoped>
.new-view {
  display: grid;
  gap: 0.4rem;
  max-width: 28rem;
}
</style>
