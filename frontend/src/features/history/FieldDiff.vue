<script setup lang="ts">
// A field-by-field diff, as the server computed it. This component draws; it
// never decides what counts as a change. Every value goes through `{{ }}`, which
// escapes: a name that looks like markup is shown as text.
import { reactive } from 'vue'

defineProps<{ changes: { field: string; before?: unknown; after?: unknown }[] }>()

/** A document body or a saved query can be long; a table cell is not a reader. */
const SHOWN = 300

const opened = reactive(new Set<string>())

function text(value: unknown): string {
  if (value === null || value === undefined) {
    return '—'
  }
  return typeof value === 'string' ? value : (JSON.stringify(value) ?? '—')
}

function shown(value: unknown, key: string): string {
  const whole = text(value)
  return opened.has(key) || whole.length <= SHOWN ? whole : `${whole.slice(0, SHOWN)}…`
}

function cut(value: unknown, key: string): number {
  const length = text(value).length
  return length > SHOWN && !opened.has(key) ? length : 0
}
</script>

<template>
  <p v-if="changes.length === 0" class="empty">Aucun champ modifié.</p>
  <table v-else class="diff">
    <thead>
      <tr>
        <th scope="col">Champ</th>
        <th scope="col">Avant</th>
        <th scope="col">Après</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="change in changes" :key="change.field">
        <th scope="row">{{ change.field }}</th>
        <td v-for="side in ['before', 'after'] as const" :key="side" :class="side">
          {{ shown(change[side], `${change.field}/${side}`) }}
          <template v-if="cut(change[side], `${change.field}/${side}`)">
            <em class="cut">
              (tronqué, {{ cut(change[side], `${change.field}/${side}`) }} caractères)
            </em>
            <button
              type="button"
              class="secondary"
              @click="opened.add(`${change.field}/${side}`)"
            >
              Tout afficher
            </button>
          </template>
        </td>
      </tr>
    </tbody>
  </table>
</template>

<style scoped>
.diff {
  border-collapse: collapse;
  width: 100%;
}
th,
td {
  text-align: left;
  padding: 0.25rem 0.5rem;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
  overflow-wrap: anywhere;
}
.before {
  color: var(--danger, #b3261e);
}
.after {
  color: var(--success, #1b6e3a);
}
.cut {
  opacity: 0.7;
}
.empty {
  opacity: 0.6;
  font-style: italic;
}
</style>
