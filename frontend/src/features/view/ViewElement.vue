<script setup lang="ts">
// One element, read: its facts, its links in both directions (each one a link),
// and its documents, rendered to safe HTML by the server (docs/adr/0042).
// The relation filter is in the URL (`?relation=`), like the neighbourhood's.
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import { useElementDetail } from '../elements/useElementDetail'
import { useMetamodel } from '../metamodel/useMetamodel'
import { RELATIONSHIP_LABELS, verbOf } from '../relationships/labels'

type GraphRead = components['schemas']['GraphRead']
type DocumentSummaryRead = components['schemas']['DocumentSummaryRead']
type RelationshipType = components['schemas']['RelationshipType']

const route = useRoute()
const router = useRouter()
const detail = useElementDetail()
const metamodel = useMetamodel()
const links = useLatestRequest()
const docs = useLatestRequest()
const reading = useLatestRequest()

const graph = ref<GraphRead>({ elements: [], relationships: [] })
const documents = ref<DocumentSummaryRead[]>([])
const opened = ref('')

const id = computed(() => String(route.params.id))
const relation = computed(() =>
  typeof route.query.relation === 'string' ? route.query.relation : '',
)
const names = computed(() => new Map(graph.value.elements.map((e) => [e.id, e.name])))
const rows = computed(() =>
  graph.value.relationships.map((link) => {
    const outgoing = link.source_id === id.value
    const otherId = outgoing ? link.target_id : link.source_id
    return {
      id: link.id,
      outgoing,
      verb: verbOf(link.relationship_type, link.access_type),
      otherId,
      otherName: names.value.get(otherId) ?? 'élément inconnu',
    }
  }),
)

async function load(): Promise<void> {
  opened.value = ''
  await detail.open(id.value)
  await links.run(
    async (signal) =>
      unwrap(
        await api.GET('/elements/{element_id}/relationships', {
          params: {
            path: { element_id: id.value },
            query: relation.value ? { relationship_type: [relation.value as RelationshipType] } : {},
          },
          signal,
        }),
      ),
    (answer) => {
      graph.value = answer
    },
  )
  await docs.run(
    async (signal) =>
      unwrap(
        await api.GET('/elements/{element_id}/documents', {
          params: { path: { element_id: id.value } },
          signal,
        }),
      ),
    (answer) => {
      documents.value = answer
    },
  )
}

async function read(documentId: string): Promise<void> {
  await reading.run(
    async (signal) =>
      unwrap(
        await api.GET('/documents/{document_id}/html', {
          params: { path: { document_id: documentId } },
          parseAs: 'text',
          signal,
        }),
      ),
    (html) => {
      opened.value = html
    },
  )
}

function filter(event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  void router.replace({ query: { ...route.query, relation: value || undefined } })
}

onMounted(async () => {
  await metamodel.load()
})
watch(
  [id, relation],
  () => {
    void load()
  },
  { immediate: true },
)
</script>

<template>
  <article class="view-element">
    <p v-if="detail.status.value === 'error'" class="banner banner--error" role="alert">
      {{ detail.error.value }}
    </p>
    <template v-if="detail.element.value">
      <h2>{{ detail.element.value.name }}</h2>
      <p class="hint">{{ metamodel.labelOf(detail.element.value.element_type) }}</p>
      <p v-if="detail.element.value.description">{{ detail.element.value.description }}</p>
      <dl v-if="Object.keys(detail.element.value.properties).length">
        <template v-for="(value, key) in detail.element.value.properties" :key="key">
          <dt>{{ key }}</dt>
          <dd>{{ value }}</dd>
        </template>
      </dl>
    </template>

    <h3>Relations</h3>
    <label for="view-relation">Type de relation</label>
    <select id="view-relation" :value="relation" @change="filter">
      <option value="">Toutes</option>
      <option v-for="type in metamodel.byStrength.value" :key="type.value" :value="type.value">
        {{ RELATIONSHIP_LABELS[type.value] }}
      </option>
    </select>
    <p v-if="links.status.value === 'error'" class="banner banner--error" role="alert">
      {{ links.error.value }}
    </p>
    <p v-else-if="links.status.value === 'ready' && rows.length === 0" class="hint">
      Aucune relation.
    </p>
    <ul>
      <li v-for="row in rows" :key="row.id">
        <span v-if="!row.outgoing" aria-label="entrante">←</span>
        {{ row.verb }}
        <RouterLink :to="{ name: 'view-element', params: { id: row.otherId } }">{{
          row.otherName
        }}</RouterLink>
      </li>
    </ul>

    <h3>Documents</h3>
    <p v-if="docs.status.value === 'ready' && documents.length === 0" class="hint">
      Aucun document.
    </p>
    <ul>
      <li v-for="document in documents" :key="document.id">
        <button type="button" class="secondary" @click="read(document.id)">
          {{ document.filename }}
        </button>
      </li>
    </ul>
    <p v-if="reading.error.value" class="banner banner--error" role="alert">
      {{ reading.error.value }}
    </p>
    <!-- Safe: the server renders markdown with raw HTML off, images off and
         dangerous link schemes refused, and tests that (docs/adr/0042). -->
    <!-- eslint-disable-next-line vue/no-v-html -->
    <div v-if="opened" class="markdown" v-html="opened" />
  </article>
</template>
