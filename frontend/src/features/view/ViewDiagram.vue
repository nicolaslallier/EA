<script setup lang="ts">
// A diagram, read: the server's own drawing (docs/adr/0043) as an image — fetched
// with the login's header and shown from a blob URL, because an `<img src>`
// cannot carry a bearer token and a token never goes into a URL — and under it
// the list of its elements, each a link, which is also the accessible reading.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import ReportDialog from '../reports/ReportDialog.vue'

type DiagramRead = components['schemas']['DiagramRead']

const route = useRoute()
const read = useLatestRequest()
const drawing = useLatestRequest()

const id = computed(() => String(route.params.id))
const diagram = ref<DiagramRead | null>(null)
const imageUrl = ref('')
const zoom = ref(1)

function release(): void {
  if (imageUrl.value) {
    URL.revokeObjectURL(imageUrl.value)
  }
  imageUrl.value = ''
}

async function load(diagramId: string): Promise<void> {
  diagram.value = null
  release()
  await read.run(
    async (signal) =>
      unwrap(
        await api.GET('/diagrams/{diagram_id}', {
          params: { path: { diagram_id: diagramId } },
          signal,
        }),
      ),
    (answer) => {
      diagram.value = answer
    },
  )
  if (!diagram.value) {
    return
  }
  await drawing.run(
    async (signal) =>
      unwrap(
        await api.GET('/diagrams/{diagram_id}/render.svg', {
          params: { path: { diagram_id: diagramId } },
          parseAs: 'blob',
          signal,
        }),
      ),
    (blob) => {
      release()
      imageUrl.value = URL.createObjectURL(blob)
    },
  )
}

function step(by: number): void {
  zoom.value = Math.min(3, Math.max(0.4, zoom.value + by))
}

onBeforeUnmount(release)
watch(
  id,
  (value) => {
    void load(value)
  },
  { immediate: true },
)
</script>

<template>
  <article class="view-diagram">
    <p v-if="read.error.value" class="banner banner--error" role="alert">{{ read.error.value }}</p>
    <p v-if="drawing.error.value" class="banner banner--error" role="alert">
      {{ drawing.error.value }}
    </p>
    <template v-if="diagram">
      <header>
        <h2>{{ diagram.name }}</h2>
        <p v-if="diagram.description" class="hint">{{ diagram.description }}</p>
        <ReportDialog subject-kind="diagram" :subject-id="diagram.id" download-only />
      </header>

      <div class="view-diagram__zoom" role="group" aria-label="Zoom">
        <button type="button" class="secondary" aria-label="Réduire" @click="step(-0.25)">−</button>
        <span>{{ Math.round(zoom * 100) }} %</span>
        <button type="button" class="secondary" aria-label="Agrandir" @click="step(0.25)">+</button>
      </div>
      <div class="view-diagram__frame">
        <img
          v-if="imageUrl"
          :src="imageUrl"
          :alt="`Diagramme ${diagram.name} : ${diagram.elements.length} éléments, ${diagram.relationships.length} relations`"
          :style="{ width: `${zoom * 100}%`, maxWidth: 'none' }"
        />
      </div>

      <h3>Éléments du diagramme</h3>
      <ul>
        <li v-for="element in diagram.elements" :key="element.id">
          <RouterLink :to="{ name: 'view-element', params: { id: element.id } }">{{
            element.name
          }}</RouterLink>
        </li>
      </ul>
    </template>
  </article>
</template>

<style scoped>
.view-diagram__frame {
  overflow: auto;
  border: 1px solid var(--border);
}
.view-diagram__zoom {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin: 0.75rem 0;
}
</style>
