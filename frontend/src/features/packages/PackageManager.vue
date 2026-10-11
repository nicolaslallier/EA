<script setup lang="ts">
// The editor's controls for the tree: create, rename, move, delete a package.
//
// A reader gets nothing — the API refuses their writes anyway (docs/adr/0032);
// this only spares them controls that could not work. Every rule (a taken
// name, a cycle, a tree too deep, a package that still holds something) is the
// API's: its refusal is shown as it comes, and the package is left as it was.
import { computed, ref, watch } from 'vue'

import { messageOf } from '../../lib/api'
import { useMe } from '../../lib/me'
import { ROOT, type PackageRead } from './packageTree'
import type { usePackages } from './usePackages'

const props = defineProps<{ store: ReturnType<typeof usePackages>; selected: string }>()
const emit = defineEmits<{ select: [value: string] }>()

const { canWrite } = useMe()

type Mode = 'create' | 'rename' | 'move' | 'delete'

const mode = ref<Mode | null>(null)
const name = ref('')
/** The parent chosen by "Déplacer", or what to do with the contents on "Supprimer". */
const target = ref('')
const failure = ref('')
const busy = ref(false)

/** The selected package — or nothing, for `''`, the root, and an id that no longer exists. */
const current = computed<PackageRead | null>(
  () => props.store.byId.value.get(props.selected) ?? null,
)

const candidates = computed(() =>
  props.store.packages.value
    .filter((entry) => entry.id !== current.value?.id)
    .sort((one, other) => one.path.localeCompare(other.path, 'fr', { sensitivity: 'base' })),
)

const LABELS: Record<Mode, string> = {
  create: 'Nouveau paquet',
  rename: 'Renommer',
  move: 'Déplacer',
  delete: 'Supprimer',
}

// A form belongs to the package it was opened on: another selection closes it,
// or "Confirmer" would apply what was typed for one package to the next.
watch(
  () => props.selected,
  () => {
    mode.value = null
    failure.value = ''
  },
)

function open(next: Mode): void {
  failure.value = ''
  mode.value = next
  name.value = next === 'rename' ? (current.value?.name ?? '') : ''
  // "Déplacer" starts on the current parent; "Supprimer" starts on "refuse if not empty".
  target.value = next === 'move' ? (current.value?.parent_id ?? ROOT) : ''
}

async function submit(): Promise<void> {
  const here = current.value
  busy.value = true
  failure.value = ''
  try {
    if (mode.value === 'create') {
      const created = await props.store.create({ name: name.value, parent_id: here?.id ?? null })
      emit('select', created.id)
    } else if (here && mode.value === 'rename') {
      await props.store.update(here.id, { name: name.value })
    } else if (here && mode.value === 'move') {
      await props.store.update(here.id, { parent_id: target.value === ROOT ? null : target.value })
    } else if (here && mode.value === 'delete') {
      await props.store.remove(here.id, target.value || undefined)
      emit('select', '')
    }
    mode.value = null
  } catch (caught) {
    // Kept open, with what was typed: the person fixes the name or picks a target.
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div v-if="canWrite" class="manager">
    <div class="bar" role="toolbar" aria-label="Gérer les paquets">
      <button type="button" @click="open('create')">Nouveau paquet</button>
      <button type="button" :disabled="!current" @click="open('rename')">Renommer</button>
      <button type="button" :disabled="!current" @click="open('move')">Déplacer</button>
      <button type="button" class="secondary" :disabled="!current" @click="open('delete')">
        Supprimer
      </button>
    </div>

    <form v-if="mode" class="form" :aria-label="LABELS[mode]" @submit.prevent="submit">
      <div v-if="mode === 'create' || mode === 'rename'" class="field">
        <label for="package-name">Nom du paquet</label>
        <input id="package-name" v-model="name" maxlength="200" autocomplete="off" />
        <small v-if="mode === 'create'">
          {{ current ? `Dans « ${current.path} ».` : 'À la racine.' }}
        </small>
      </div>

      <div v-if="mode === 'move'" class="field">
        <label for="package-parent">Nouveau parent</label>
        <select id="package-parent" v-model="target">
          <option :value="ROOT">Racine</option>
          <option v-for="entry in candidates" :key="entry.id" :value="entry.id">
            {{ entry.path }}
          </option>
        </select>
      </div>

      <template v-if="mode === 'delete'">
        <p>Supprimer « {{ current?.path }} » ? Les éléments et diagrammes ne sont jamais supprimés.</p>
        <div class="field">
          <label for="package-contents">Contenu à déplacer vers…</label>
          <select id="package-contents" v-model="target">
            <option value="">Nulle part (refusé si le paquet n'est pas vide)</option>
            <option :value="ROOT">Racine</option>
            <option v-for="entry in candidates" :key="entry.id" :value="entry.id">
              {{ entry.path }}
            </option>
          </select>
        </div>
      </template>

      <p v-if="failure" class="banner banner--error" role="alert">{{ failure }}</p>

      <div class="actions">
        <button type="submit" :disabled="busy">Confirmer</button>
        <button type="button" class="secondary" @click="mode = null">Renoncer</button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.manager {
  display: grid;
  gap: 0.6rem;
}
.bar,
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}
.form {
  display: grid;
  gap: 0.6rem;
  padding: 0.6rem;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.field {
  display: grid;
  gap: 0.25rem;
}
label {
  font-size: 0.85rem;
  font-weight: 600;
}
input,
select {
  font: inherit;
  padding: 0.4rem 0.6rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: inherit;
  color: inherit;
}
button {
  font: inherit;
  padding: 0.4rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
}
button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.secondary {
  background: transparent;
}
.banner {
  margin: 0;
  padding: 0.5rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.banner--error {
  border-color: #c62828;
  color: #c62828;
}
</style>
