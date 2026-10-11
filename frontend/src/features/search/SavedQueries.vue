<script setup lang="ts">
// The shared, named filters of the catalogue.
//
// Anyone who can log in lists and opens them; an editor also keeps the
// filter on screen under a name, renames a query, replaces its filter with the
// one on screen, deletes it. The API refuses a reader's write anyway
// (docs/adr/0032) — hiding the controls only spares them ones that could not work.
import { computed, ref } from 'vue'

import { messageOf } from '../../lib/api'
import { useMe } from '../../lib/me'
import type { Query } from './queryState'
import type { useSavedQueries } from './useSavedQueries'

const props = defineProps<{
  store: ReturnType<typeof useSavedQueries>
  activeId: string
  /** Settles what is still being typed, and answers with the filter on screen. */
  settle: () => Query
}>()
const emit = defineEmits<{ open: [id: string]; removed: [id: string] }>()

const { canWrite } = useMe()

const name = ref('')
const description = ref('')
const renaming = ref('')
const renameTo = ref('')
const confirming = ref('')
const failure = ref('')
const busy = ref(false)

const confirmingName = computed(
  () => props.store.queries.value.find((entry) => entry.id === confirming.value)?.name ?? '',
)

async function guarded(action: () => Promise<void>): Promise<void> {
  busy.value = true
  failure.value = ''
  try {
    await action()
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

async function save(): Promise<void> {
  await guarded(async () => {
    const created = await props.store.create(name.value.trim(), description.value, props.settle())
    name.value = ''
    description.value = ''
    emit('open', created.id)
  })
}

function startRename(id: string, current: string): void {
  renaming.value = id
  renameTo.value = current
}

async function rename(): Promise<void> {
  await guarded(async () => {
    await props.store.update(renaming.value, { name: renameTo.value })
    renaming.value = ''
  })
}

async function replaceFilter(id: string): Promise<void> {
  await guarded(async () => {
    await props.store.update(id, { query: props.settle() })
    // The same address, a new filter: the screen reloads it.
    emit('open', id)
  })
}

async function remove(): Promise<void> {
  await guarded(async () => {
    const id = confirming.value
    await props.store.remove(id)
    confirming.value = ''
    emit('removed', id)
  })
}
</script>

<template>
  <section class="saved" aria-label="Requêtes enregistrées">
    <h3>Requêtes enregistrées</h3>

    <form v-if="canWrite" class="save" aria-label="Enregistrer le filtre actuel" @submit.prevent="save">
      <div class="field">
        <label for="saved-name">Nom de la requête</label>
        <input id="saved-name" v-model="name" maxlength="200" autocomplete="off" />
      </div>
      <div class="field">
        <label for="saved-description">Description</label>
        <input id="saved-description" v-model="description" autocomplete="off" />
      </div>
      <button type="submit" :disabled="busy || !name.trim()">Enregistrer</button>
    </form>

    <p v-if="failure" class="banner banner--error" role="alert">{{ failure }}</p>
    <p v-if="store.status.value === 'error'" class="banner banner--error" role="alert">
      {{ store.error.value }}
    </p>

    <p v-if="confirming" class="banner banner--confirm" role="alert">
      Supprimer « {{ confirmingName }} » ? Les éléments qu'elle trouve sont conservés.
      <button type="button" :disabled="busy" @click="remove">Confirmer</button>
      <button type="button" class="secondary" @click="confirming = ''">Renoncer</button>
    </p>

    <p v-if="store.status.value === 'ready' && store.queries.value.length === 0" class="hint">
      Aucune requête enregistrée pour l'instant.
    </p>

    <ul class="list">
      <li v-for="saved in store.queries.value" :key="saved.id">
        <form v-if="renaming === saved.id" class="inline" @submit.prevent="rename">
          <input v-model="renameTo" :aria-label="`Nouveau nom de ${saved.name}`" maxlength="200" />
          <button type="submit" :disabled="busy || !renameTo.trim()">Valider</button>
          <button type="button" class="secondary" @click="renaming = ''">Renoncer</button>
        </form>
        <template v-else>
          <button
            type="button"
            class="link"
            :aria-current="activeId === saved.id ? 'true' : undefined"
            :title="saved.description || undefined"
            @click="emit('open', saved.id)"
          >
            {{ saved.name }}
          </button>
          <span class="hint">par {{ saved.created_by }}</span>
          <span v-if="canWrite" class="actions">
            <button
              type="button"
              :aria-label="`Renommer ${saved.name}`"
              @click="startRename(saved.id, saved.name)"
            >
              Renommer
            </button>
            <button
              type="button"
              :aria-label="`Remplacer le filtre de ${saved.name} par le filtre actuel`"
              @click="replaceFilter(saved.id)"
            >
              Remplacer le filtre
            </button>
            <button
              type="button"
              class="secondary"
              :aria-label="`Supprimer ${saved.name}`"
              @click="confirming = saved.id"
            >
              Supprimer
            </button>
          </span>
        </template>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.saved {
  display: grid;
  gap: 0.6rem;
}
h3 {
  margin: 0;
}
.save {
  display: grid;
  gap: 0.4rem;
}
.field {
  display: grid;
  gap: 0.25rem;
}
label {
  font-size: 0.85rem;
  font-weight: 600;
}
.list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 0.4rem;
}
.list li,
.inline,
.actions {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.4rem;
}
.link {
  padding: 0;
  border: none;
  background: none;
  color: inherit;
  font: inherit;
  font-weight: 600;
  text-decoration: underline;
  text-underline-offset: 2px;
  cursor: pointer;
}
.link[aria-current='true'] {
  text-decoration-thickness: 3px;
}
input {
  font: inherit;
  padding: 0.35rem 0.55rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: inherit;
  color: inherit;
}
button {
  font: inherit;
  padding: 0.35rem 0.7rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
}
.link {
  border: none;
}
button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.secondary {
  background: transparent;
}
.hint {
  opacity: 0.75;
  font-size: 0.9em;
}
.banner {
  margin: 0;
  padding: 0.5rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}
.banner--error {
  border-color: #c62828;
  color: #c62828;
}
.banner--confirm {
  border-color: #ef6c00;
}
</style>
