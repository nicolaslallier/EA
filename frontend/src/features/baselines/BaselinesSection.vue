<script setup lang="ts">
// Les baselines (docs/adr/0045) : des copies figées du modèle, comparables.
//
// La paire comparée est dans l'URL (`?a=` la baseline, `?b=` l'autre ; `b` absent
// = le modèle courant) : une question qu'on partage et qu'on remonte avec
// « précédent ». L'API décide qui écrit ; ici on cache seulement ce qu'un
// lecteur ne peut pas faire.
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { messageOf } from '../../lib/api'
import { useMe } from '../../lib/me'
import DiffColumn from './DiffColumn.vue'
import RestoreDialog from './RestoreDialog.vue'
import { useBaselines, type Baseline } from './useBaselines'

const route = useRoute()
const router = useRouter()
const { canWrite } = useMe()
const store = useBaselines()

const name = ref('')
const description = ref('')
const failure = ref('')
const busy = ref(false)
const restoring = ref<Baseline | null>(null)
const deleting = ref<Baseline | null>(null)

function param(key: string): string {
  const value = route.query[key]
  return typeof value === 'string' ? value : ''
}

const a = computed(() => param('a'))
const b = computed(() => param('b'))
const subject = computed(() => store.baselines.value.find((candidate) => candidate.id === a.value))
const other = computed(() => store.baselines.value.find((candidate) => candidate.id === b.value))

void store.load()
watch(
  [a, b],
  ([first, second]) => {
    if (first) {
      void store.compare(first, second || undefined)
    } else {
      store.forgetDiff()
    }
  },
  { immediate: true },
)

/** Choosing what to look at is a new question: it gets its own history entry. */
function compareWith(id: string, against?: string): void {
  void router.push({ query: { a: id, b: against } })
}

async function capture(): Promise<void> {
  busy.value = true
  failure.value = ''
  try {
    await store.capture(name.value, description.value)
    name.value = ''
    description.value = ''
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

async function confirmDelete(): Promise<void> {
  const doomed = deleting.value
  if (!doomed) {
    return
  }
  busy.value = true
  failure.value = ''
  try {
    await store.remove(doomed.id)
    // A pair that names a baseline that no longer exists is a dead link.
    if (a.value === doomed.id) {
      await router.replace({ query: {} })
    } else if (b.value === doomed.id) {
      await router.replace({ query: { a: a.value } })
    }
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
    deleting.value = null
  }
}

const diffIsEmpty = computed(() => {
  const found = store.diff.value
  return (
    found !== null && Object.values(found).every((entries) => (entries as unknown[]).length === 0)
  )
})

function onRestored(): void {
  restoring.value = null
  void store.load()
  if (a.value) {
    void store.compare(a.value, b.value || undefined)
  }
}

/** The server speaks UTC; say so rather than let the reader assume their own zone. */
function when(iso: string): string {
  const moment = new Date(iso)
  return Number.isNaN(moment.getTime())
    ? iso
    : `${moment.toISOString().slice(0, 16).replace('T', ' ')} UTC`
}
</script>

<template>
  <section aria-labelledby="baselines-title">
    <h2 id="baselines-title">Baselines</h2>
    <p class="note">
      Une baseline est une copie figée du modèle (éléments, relations, positions dans les
      diagrammes). Les documents joints aux éléments n'en font pas partie.
    </p>
    <p v-if="failure" role="alert">{{ failure }}</p>

    <form v-if="canWrite" class="capture" @submit.prevent="capture">
      <label>Nom <input v-model="name" type="text" required maxlength="200" /></label>
      <label>Description <input v-model="description" type="text" maxlength="2000" /></label>
      <button type="submit" :disabled="busy">Capturer</button>
    </form>

    <p v-if="deleting" class="confirm" role="alert">
      Supprimer la baseline « {{ deleting.name }} » ? Le modèle n'est pas touché.
      <button type="button" :disabled="busy" @click="confirmDelete">Confirmer</button>
      <button type="button" class="secondary" @click="deleting = null">Renoncer</button>
    </p>

    <p v-if="store.status.value === 'loading' && store.baselines.value.length === 0" role="status">
      Chargement…
    </p>
    <p v-else-if="store.status.value === 'error'" role="alert">
      {{ store.error.value }}
    </p>
    <p
      v-else-if="store.status.value === 'ready' && store.baselines.value.length === 0"
      class="empty"
    >
      Aucune baseline.
    </p>

    <ul class="baselines">
      <li v-for="baseline in store.baselines.value" :key="baseline.id">
        <strong :id="`baseline-${baseline.id}`">{{ baseline.name }}</strong>
        <span>
          {{ baseline.element_count }} éléments, {{ baseline.relationship_count }} relations,
          {{ baseline.box_count }} boîtes — {{ baseline.created_by }},
          {{ when(baseline.created_at) }}
        </span>
        <span v-if="baseline.description" class="description">{{ baseline.description }}</span>
        <RouterLink :to="{ name: 'history', query: { kind: 'baseline', entity: baseline.id } }">
          Historique
        </RouterLink>
        <button
          type="button"
          :aria-describedby="`baseline-${baseline.id}`"
          @click="compareWith(baseline.id)"
        >
          Comparer au modèle courant
        </button>
        <button
          v-if="a && a !== baseline.id"
          type="button"
          :aria-describedby="`baseline-${baseline.id}`"
          @click="compareWith(a, baseline.id)"
        >
          Comparer à « {{ subject?.name ?? a }} »
        </button>
        <template v-if="canWrite">
          <button
            type="button"
            :aria-describedby="`baseline-${baseline.id}`"
            @click="restoring = baseline"
          >
            Restaurer
          </button>
          <button
            type="button"
            class="secondary"
            :aria-describedby="`baseline-${baseline.id}`"
            @click="deleting = baseline"
          >
            Supprimer
          </button>
        </template>
      </li>
    </ul>

    <section v-if="a" class="diff" aria-label="Différences">
      <h3>
        « {{ subject?.name ?? a }} » →
        {{ b ? `« ${other?.name ?? b} »` : 'modèle courant' }}
      </h3>
      <p v-if="store.diffStatus.value === 'loading'" role="status">Comparaison…</p>
      <p v-else-if="store.diffStatus.value === 'error'" role="alert">
        {{ store.diffError.value }}
      </p>
      <template v-else-if="store.diff.value">
        <p v-if="diffIsEmpty" class="empty">Aucune différence.</p>
        <div v-else class="columns">
          <DiffColumn
            title="Ajoutés"
            :entries="[...store.diff.value.elements_added, ...store.diff.value.relationships_added]"
          />
          <DiffColumn
            title="Supprimés"
            :entries="[
              ...store.diff.value.elements_removed,
              ...store.diff.value.relationships_removed,
            ]"
          />
          <DiffColumn
            title="Modifiés"
            :entries="[
              ...store.diff.value.elements_modified,
              ...store.diff.value.relationships_modified,
            ]"
          />
        </div>
      </template>
    </section>

    <RestoreDialog
      v-if="restoring && canWrite"
      :baseline="restoring"
      @close="restoring = null"
      @restored="onRestored"
    />
  </section>
</template>

<style scoped>
.note,
.empty,
.description {
  opacity: 0.7;
}
.empty {
  font-style: italic;
}
.capture {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  align-items: end;
  margin-bottom: 1rem;
}
.confirm {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  align-items: baseline;
}
.baselines {
  list-style: none;
  padding: 0;
  display: grid;
  gap: 0.5rem;
}
.baselines li {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  align-items: baseline;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.5rem 0.8rem;
}
.columns {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
  gap: 1rem;
}
</style>
