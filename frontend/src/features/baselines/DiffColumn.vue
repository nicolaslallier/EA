<script setup lang="ts">
// One column of a baseline diff: what was added, removed or modified. The server
// sorted it; this draws it. A model can differ by thousands of rows, so only the
// first `SHOWN_ROWS` are drawn until asked. Every value goes through `{{ }}`.
import { computed, ref } from 'vue'

import FieldDiff from '../history/FieldDiff.vue'

type Entry = {
  id: string
  kind: string
  label: string
  type: string
  changes?: { field: string; before?: unknown; after?: unknown }[]
}

const SHOWN_ROWS = 100

const props = defineProps<{ title: string; entries: Entry[] }>()
const all = ref(false)
const shown = computed(() => (all.value ? props.entries : props.entries.slice(0, SHOWN_ROWS)))
const hidden = computed(() => props.entries.length - shown.value.length)
</script>

<template>
  <div class="column">
    <h4>{{ title }} ({{ entries.length }})</h4>
    <ul>
      <li v-for="entry in shown" :key="`${entry.kind}/${entry.id}`">
        <span class="kind">{{ entry.kind === 'relationship' ? 'lien' : entry.type }}</span>
        {{ entry.label }}
        <FieldDiff v-if="entry.changes" :changes="entry.changes" />
      </li>
    </ul>
    <button v-if="hidden > 0" type="button" class="secondary" @click="all = true">
      Afficher les {{ hidden }} autres
    </button>
  </div>
</template>

<style scoped>
ul {
  list-style: none;
  padding: 0;
  display: grid;
  gap: 0.35rem;
}
li {
  overflow-wrap: anywhere;
}
.kind {
  opacity: 0.6;
  font-size: 0.8rem;
  margin-right: 0.3rem;
}
</style>
