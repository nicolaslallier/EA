<script setup lang="ts">
// The reader's entry point: the saved diagrams, and a search over element names.
// The term is in the URL (`?q=`), so a search can be linked to and walked back.
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import { useDiagrams } from '../diagrams/useDiagrams'

type ElementRead = components['schemas']['ElementRead']
type PackageRead = components['schemas']['PackageRead']

const route = useRoute()
const router = useRouter()
const diagrams = useDiagrams()
const search = useLatestRequest()
const tree = useLatestRequest()

const term = ref(typeof route.query.q === 'string' ? route.query.q : '')
const found = ref<ElementRead[]>([])
const roots = ref<PackageRead[]>([])

async function run(q: string): Promise<void> {
  if (!q.trim()) {
    search.cancel()
    found.value = []
    return
  }
  await search.run(
    async (signal) =>
      unwrap(await api.GET('/elements', { params: { query: { search: q.trim(), limit: 20 } }, signal })),
    (page) => {
      found.value = page.items
    },
  )
}

function submit(): void {
  void router.replace({ query: { q: term.value.trim() || undefined } })
}

watch(
  () => route.query.q,
  (q) => {
    void run(typeof q === 'string' ? q : '')
  },
  { immediate: true },
)
onMounted(async () => {
  await diagrams.load()
  await tree.run(
    async (signal) => unwrap(await api.GET('/packages', { signal })),
    (answer) => {
      roots.value = answer
        .filter((entry) => entry.parent_id === null)
        .sort((one, other) => one.name.localeCompare(other.name, 'fr', { sensitivity: 'base' }))
    },
  )
})
</script>

<template>
  <div class="view-home">
    <h2>Consultation</h2>

    <form role="search" @submit.prevent="submit">
      <label for="view-search">Chercher un élément</label>
      <input id="view-search" v-model="term" type="search" maxlength="200" autocomplete="off" />
      <button type="submit">Chercher</button>
    </form>
    <p v-if="search.error.value" class="banner banner--error" role="alert">
      {{ search.error.value }}
    </p>
    <ul v-if="found.length" aria-label="Éléments trouvés">
      <li v-for="element in found" :key="element.id">
        <RouterLink :to="{ name: 'view-element', params: { id: element.id } }">{{
          element.name
        }}</RouterLink>
      </li>
    </ul>

    <h3>Paquets</h3>
    <p v-if="tree.error.value" class="banner banner--error" role="alert">{{ tree.error.value }}</p>
    <p v-else-if="tree.status.value === 'ready' && roots.length === 0" class="hint">
      Aucun paquet.
    </p>
    <ul v-else aria-label="Paquets">
      <li v-for="entry in roots" :key="entry.id">
        <RouterLink :to="{ name: 'view-package', params: { id: entry.id } }">{{
          entry.name
        }}</RouterLink>
      </li>
    </ul>

    <h3>Diagrammes</h3>
    <p v-if="diagrams.status.value === 'error'" class="banner banner--error" role="alert">
      {{ diagrams.error.value }}
    </p>
    <p v-else-if="diagrams.status.value === 'ready' && diagrams.diagrams.value.length === 0" class="hint">
      Aucun diagramme.
    </p>
    <ul>
      <li v-for="diagram in diagrams.diagrams.value" :key="diagram.id">
        <RouterLink :to="{ name: 'view-diagram', params: { id: diagram.id } }">{{
          diagram.name
        }}</RouterLink>
        <span class="hint">{{ diagram.node_count }} élément{{ diagram.node_count > 1 ? 's' : '' }}</span>
      </li>
    </ul>
  </div>
</template>
