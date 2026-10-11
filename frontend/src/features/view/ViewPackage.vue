<script setup lang="ts">
// A package, read: its sub-packages, its elements and its diagrams, each a link.
// The package is in the path (`/view/package/:id`), so a reading can be linked to
// and walked back (docs/adr/0064). Only `GET`s: the same reads as the catalogue.
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import ReportDialog from '../reports/ReportDialog.vue'

type ElementRead = components['schemas']['ElementRead']
type PackageRead = components['schemas']['PackageRead']
type DiagramSummaryRead = components['schemas']['DiagramSummaryRead']

/** One page of elements: a larger package says so, and the catalogue holds the rest. */
const PAGE = 100

const route = useRoute()
const packages = useLatestRequest()
const contents = useLatestRequest()

const id = computed(() => String(route.params.id))
const everything = ref<PackageRead[]>([])
const elements = ref<ElementRead[]>([])
const total = ref(0)
const diagrams = ref<DiagramSummaryRead[]>([])

const current = computed(() => everything.value.find((entry) => entry.id === id.value) ?? null)
const children = computed(() =>
  everything.value
    .filter((entry) => entry.parent_id === id.value)
    .sort((one, other) => one.name.localeCompare(other.name, 'fr', { sensitivity: 'base' })),
)

async function load(packageId: string): Promise<void> {
  await packages.run(
    async (signal) => unwrap(await api.GET('/packages', { signal })),
    (answer) => {
      everything.value = answer
    },
  )
  await contents.run(
    async (signal) => {
      const [found, drawn] = await Promise.all([
        api.GET('/elements', {
          params: {
            query: { package: packageId, include_subpackages: false, limit: PAGE },
          },
          signal,
        }),
        api.GET('/diagrams', {
          params: { query: { package: packageId, include_subpackages: false } },
          signal,
        }),
      ])
      return { page: unwrap(found), drawn: unwrap(drawn) }
    },
    ({ page, drawn }) => {
      elements.value = page.items
      total.value = page.total
      diagrams.value = drawn
    },
  )
}

watch(
  id,
  (value) => {
    void load(value)
  },
  { immediate: true },
)
</script>

<template>
  <article class="view-package">
    <p v-if="packages.error.value" class="banner banner--error" role="alert">
      {{ packages.error.value }}
    </p>
    <p v-else-if="contents.error.value" class="banner banner--error" role="alert">
      {{ contents.error.value }}
    </p>
    <template v-if="current">
      <p class="hint">
        <RouterLink :to="{ name: 'view-home' }">Consultation</RouterLink> › {{ current.path }}
      </p>
      <h2>{{ current.name }}</h2>
      <p v-if="current.description">{{ current.description }}</p>
      <ReportDialog subject-kind="package" :subject-id="current.id" download-only />

      <h3>Sous-paquets</h3>
      <p v-if="children.length === 0" class="hint">Aucun sous-paquet.</p>
      <ul v-else aria-label="Sous-paquets">
        <li v-for="child in children" :key="child.id">
          <RouterLink :to="{ name: 'view-package', params: { id: child.id } }">{{
            child.name
          }}</RouterLink>
        </li>
      </ul>

      <h3>Diagrammes</h3>
      <p v-if="contents.status.value === 'ready' && diagrams.length === 0" class="hint">
        Aucun diagramme.
      </p>
      <ul v-else aria-label="Diagrammes du paquet">
        <li v-for="diagram in diagrams" :key="diagram.id">
          <RouterLink :to="{ name: 'view-diagram', params: { id: diagram.id } }">{{
            diagram.name
          }}</RouterLink>
        </li>
      </ul>

      <h3>Éléments</h3>
      <p v-if="contents.status.value === 'ready' && elements.length === 0" class="hint">
        Aucun élément directement dans ce paquet.
      </p>
      <ul v-else aria-label="Éléments du paquet">
        <li v-for="element in elements" :key="element.id">
          <RouterLink :to="{ name: 'view-element', params: { id: element.id } }">{{
            element.name
          }}</RouterLink>
        </li>
      </ul>
      <p v-if="total > elements.length" class="hint">
        {{ elements.length }} éléments sur {{ total }} : le catalogue complet les liste tous.
      </p>
    </template>
    <p v-else-if="packages.status.value === 'ready'" class="banner banner--error" role="alert">
      Ce paquet n'existe pas.
    </p>
  </article>
</template>
