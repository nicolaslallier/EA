<script setup lang="ts">
// One cell, opened: the links it holds and, for an editor, the form that adds
// one. The form offers `detail.allowed` and nothing else — the list comes from
// `GET /matrix/cell`, which asks the metamodel; no type is computed here.
import { ref, watch } from 'vue'

import { RELATIONSHIP_LABELS, verbOf } from '../relationships/labels'
import type { MatrixCellDetailRead, RelationshipType } from './useMatrix'

const props = defineProps<{
  detail: MatrixCellDetailRead
  sourceName: string
  targetName: string
  canWrite: boolean
  busy: boolean
  failure: string
}>()
const emit = defineEmits<{
  connect: [type: RelationshipType]
  disconnect: [id: string]
  close: []
}>()

const chosen = ref<RelationshipType | ''>('')
/** The link whose removal is waiting for a second click. */
const confirming = ref<string | null>(null)

// A different cell, or the same one after a write, starts the form afresh.
watch(
  () => props.detail,
  () => {
    chosen.value = ''
    confirming.value = null
  },
)

function submit(): void {
  if (chosen.value) {
    emit('connect', chosen.value)
  }
}

function remove(id: string): void {
  if (confirming.value !== id) {
    confirming.value = id
    return
  }
  confirming.value = null
  emit('disconnect', id)
}
</script>

<template>
  <aside class="cell-panel" aria-label="Liens de la cellule" @keydown.esc="emit('close')">
    <header>
      <h3>{{ sourceName }} → {{ targetName }}</h3>
      <button type="button" @click="emit('close')">Fermer</button>
    </header>

    <p v-if="failure" class="banner banner--error" role="alert">{{ failure }}</p>

    <p v-if="detail.links.length === 0" class="hint">
      Aucun lien de « {{ sourceName }} » vers « {{ targetName }} ».
    </p>
    <ul v-else>
      <li v-for="link in detail.links" :key="link.id">
        {{ sourceName }} {{ verbOf(link.relationship_type, link.access_type) }} {{ targetName }}
        <button v-if="canWrite" type="button" :disabled="busy" @click="remove(link.id)">
          {{ confirming === link.id ? 'Confirmer' : 'Supprimer' }}
        </button>
      </li>
    </ul>

    <form v-if="canWrite" @submit.prevent="submit">
      <label for="cell-relation">Nouveau lien</label>
      <select id="cell-relation" v-model="chosen">
        <option value="">Choisis une relation…</option>
        <option v-for="type in detail.allowed" :key="type" :value="type">
          {{ RELATIONSHIP_LABELS[type] }}
        </option>
      </select>
      <button type="submit" :disabled="busy || !chosen">Relier</button>
    </form>
  </aside>
</template>

<style scoped>
.cell-panel {
  margin-top: 1rem;
  padding: 0.75rem 1rem;
  border: 1px solid var(--border, #c9c4d1);
  border-radius: 6px;
}
.cell-panel header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
