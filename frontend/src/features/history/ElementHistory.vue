<script setup lang="ts">
// L'historique d'un élément, sur sa fiche. Fermé par défaut et chargé à
// l'ouverture : la fiche s'ouvre sans une requête de plus. Lecture seule.
import { watch } from 'vue'
import { RouterLink } from 'vue-router'

import { ACTION_LABELS } from './auditLabels'
import FieldDiff from './FieldDiff.vue'
import { useAuditLog } from './useAuditLog'

const props = defineProps<{ elementId: string }>()
const log = useAuditLog()

/** Ten lines are a glance; the section holds the rest. */
const GLANCE = 10

let opened = false

function load(): void {
  void log.load({ kind: 'element', entity: props.elementId }, GLANCE)
}

function onToggle(event: Event): void {
  opened = (event.target as HTMLDetailsElement).open
  if (opened) {
    load()
  }
}

// The sheet may be handed another element while the panel stays open.
watch(
  () => props.elementId,
  () => {
    if (opened) {
      load()
    }
  },
)
</script>

<template>
  <details class="history" @toggle="onToggle">
    <summary>Historique</summary>
    <p v-if="log.status.value === 'loading'" role="status">Chargement…</p>
    <p v-else-if="log.status.value === 'error'" role="alert">{{ log.error.value }}</p>
    <p v-else-if="log.status.value === 'ready' && log.events.value.length === 0" class="empty">
      Aucun événement.
    </p>
    <ul>
      <li v-for="event in log.events.value" :key="event.id">
        <strong>{{ ACTION_LABELS[event.action] }}</strong>
        — {{ event.actor_name }}, {{ event.at.slice(0, 10) }}
        <FieldDiff :changes="event.changes" />
      </li>
    </ul>
    <RouterLink :to="{ name: 'history', query: { kind: 'element', entity: elementId } }">
      Tout l’historique
    </RouterLink>
  </details>
</template>

<style scoped>
.history {
  border-top: 1px solid var(--border);
  padding-top: 0.8rem;
}
ul {
  list-style: none;
  padding: 0;
  display: grid;
  gap: 0.6rem;
}
.empty {
  opacity: 0.6;
  font-style: italic;
}
</style>
