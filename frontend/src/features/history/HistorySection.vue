<script setup lang="ts">
// Qui a changé quoi, et quand (docs/adr/0044).
//
// Les filtres et la page sont dans l'URL (`?kind=`, `?entity=`, `?actor=`,
// `?action=`, `?since=`, `?cursor=`) : une question qu'on partage et qu'on
// remonte avec « précédent ». Changer un filtre remplace l'entrée d'historique
// et repart de la première page ; tourner la page en pousse une. L'écran ne
// fait que lire : aucun contrôle d'écriture, pour personne.
import { computed, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import {
  ACTION_LABELS,
  isAction,
  isKind,
  KIND_LABELS,
  type AuditAction,
  type EntityKind,
} from './auditLabels'
import FieldDiff from './FieldDiff.vue'
import { useAuditLog, type AuditFilters } from './useAuditLog'

const route = useRoute()
const router = useRouter()
const log = useAuditLog()

function param(name: string): string {
  const value = route.query[name]
  return typeof value === 'string' ? value : ''
}

const filters = computed<AuditFilters>(() => {
  const kind = param('kind')
  const action = param('action')
  return {
    kind: isKind(kind) ? kind : undefined,
    entity: param('entity') || undefined,
    actor: param('actor') || undefined,
    action: isAction(action) ? action : undefined,
    since: param('since') || undefined,
    cursor: param('cursor') || undefined,
  }
})

function drafted() {
  return {
    kind: filters.value.kind ?? '',
    actor: filters.value.actor ?? '',
    action: filters.value.action ?? '',
    since: filters.value.since ?? '',
  }
}

// The form follows the URL, so « précédent » shows the filters of the page it returns to.
const draft = ref(drafted())
watch(filters, () => (draft.value = drafted()))

watch(filters, (value) => void log.load(value), { immediate: true })

function apply(): void {
  const next: Record<string, string> = {}
  for (const [key, value] of Object.entries(draft.value)) {
    if (value) {
      next[key] = value
    }
  }
  // The entity is not a field of the form, but it is part of the question.
  if (param('entity')) {
    next.entity = param('entity')
  }
  void router.replace({ query: next })
}

function nextPage(): void {
  if (log.nextCursor.value) {
    void router.push({ query: { ...route.query, cursor: log.nextCursor.value } })
  }
}

function firstPage(): void {
  const { cursor: _cursor, ...rest } = route.query
  void router.push({ query: rest })
}

// A line is folded until asked: fifty diffs on a page is a wall, and a diff that
// is not drawn costs nothing.
const unfolded = reactive(new Set<string>())

function toggle(id: string): void {
  if (!unfolded.delete(id)) {
    unfolded.add(id)
  }
}

const actions = Object.entries(ACTION_LABELS) as [AuditAction, string][]
const kinds = Object.entries(KIND_LABELS) as [EntityKind, string][]

/** The server speaks UTC; say so rather than let the reader assume their own zone. */
function when(iso: string): string {
  const moment = new Date(iso)
  return Number.isNaN(moment.getTime())
    ? iso
    : `${moment.toISOString().slice(0, 19).replace('T', ' ')} UTC`
}
</script>

<template>
  <section aria-labelledby="history-title">
    <h2 id="history-title">Historique</h2>

    <form class="filters" @submit.prevent="apply">
      <label>
        Type
        <select v-model="draft.kind">
          <option value="">Tous</option>
          <option v-for="[value, label] in kinds" :key="value" :value="value">{{ label }}</option>
        </select>
      </label>
      <label>
        Action
        <select v-model="draft.action">
          <option value="">Toutes</option>
          <option v-for="[value, label] in actions" :key="value" :value="value">{{ label }}</option>
        </select>
      </label>
      <label>
        Acteur
        <input v-model="draft.actor" type="text" maxlength="200" />
      </label>
      <label>
        Depuis
        <input v-model="draft.since" type="date" />
      </label>
      <button type="submit">Filtrer</button>
    </form>

    <p v-if="log.status.value === 'loading'" role="status">Chargement…</p>
    <p v-else-if="log.status.value === 'error'" role="alert">{{ log.error.value }}</p>
    <p v-else-if="log.status.value === 'ready' && log.events.value.length === 0" class="empty">
      Aucun événement.
    </p>

    <ol v-if="log.events.value.length > 0" class="events">
      <li v-for="event in log.events.value" :key="event.id" class="event">
        <header>
          <strong>{{ ACTION_LABELS[event.action] }}</strong>
          <span>{{ event.actor_name }}</span>
          <time :datetime="event.at">{{ when(event.at) }}</time>
          <span class="channel">{{ event.channel }}</span>
          <RouterLink
            v-if="event.entity_kind === 'element' && event.entity_id"
            :to="{ name: 'elements', query: { element: event.entity_id } }"
          >
            Voir l'élément
          </RouterLink>
        </header>
        <button
          type="button"
          class="secondary"
          :aria-expanded="unfolded.has(event.id)"
          @click="toggle(event.id)"
        >
          Voir les champs
        </button>
        <div v-if="unfolded.has(event.id)">
          <FieldDiff :changes="event.changes" />
          <p class="request">Requête {{ event.request_id }}</p>
        </div>
      </li>
    </ol>

    <nav v-if="filters.cursor || log.nextCursor.value" aria-label="Pages">
      <button v-if="filters.cursor" type="button" class="secondary" @click="firstPage">
        Première page
      </button>
      <button v-if="log.nextCursor.value" type="button" @click="nextPage">Page suivante</button>
    </nav>
  </section>
</template>

<style scoped>
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  align-items: end;
  margin-bottom: 1rem;
}
.events {
  list-style: none;
  padding: 0;
  display: grid;
  gap: 0.5rem;
}
.event {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.6rem 0.8rem;
}
.event header {
  display: flex;
  flex-wrap: wrap;
  gap: 0.8rem;
  align-items: baseline;
}
.channel,
.request {
  opacity: 0.6;
  font-size: 0.8rem;
}
.empty {
  opacity: 0.6;
  font-style: italic;
}
nav {
  display: flex;
  gap: 0.5rem;
  margin-top: 1rem;
}
</style>
