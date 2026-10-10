<script setup lang="ts">
// The form that builds a structured search.
//
// It owns no state of its own beyond the text being typed: every control
// computes the next query from the one it was given and emits it, so the
// screen decides what the query is. What it offers — the types, the layers,
// the relationships — is read off `/metamodel` through the composable it is
// handed, never listed here: a list in TypeScript would be a second
// metamodel (docs/adr/0012). The few words below only *translate* wire values.
import { computed, nextTick, ref, watch } from 'vue'

import { debounce } from '../../lib/debounce'
import { LAYER_LABELS, type useMetamodel } from '../metamodel/useMetamodel'
import { ROOT, type PackageRead } from '../packages/packageTree'
import { RELATIONSHIP_LABELS } from '../relationships/labels'
import { cloneQuery, emptyQuery, type PropertyTest, type Query, type RelationTest } from './queryState'

const props = defineProps<{
  modelValue: Query
  metamodel: ReturnType<typeof useMetamodel>
  packages: PackageRead[]
}>()
const emit = defineEmits<{ 'update:modelValue': [query: Query]; submit: [] }>()

/** Translations of the wire values of the API's enumerations — labels, not rules. */
const OPERATORS: Record<PropertyTest['op'], string> = {
  eq: 'est égal à',
  contains: 'contient',
  exists: 'existe',
  absent: "n'existe pas",
}
const DIRECTIONS: Record<RelationTest['direction'], string> = {
  out: 'sortante',
  in: 'entrante',
  any: 'dans un sens ou l’autre',
}
const ORDERS: Record<Query['order'], string> = {
  name: 'Nom',
  created_at: 'Création, la plus récente d’abord',
  updated_at: 'Modification, la plus récente d’abord',
}

// The form edits its own copy, so what is on screen never waits for a parent to
// hand the new query back; the parent hears each change and may replace the
// whole query (a reset, a saved query opened), which the watcher takes in.
const draft = ref<Query>(cloneQuery(props.modelValue))
watch(
  () => props.modelValue,
  (value) => {
    draft.value = cloneQuery(value)
  },
  { deep: true },
)

/** The next query: a copy of the current one, changed. The prop is never mutated. */
function edit(change: (next: Query) => void): void {
  const next = cloneQuery(draft.value)
  change(next)
  draft.value = next
  emit('update:modelValue', cloneQuery(next))
}

function reset(): void {
  // A text still waiting for its pause would otherwise come back after the reset.
  typing.cancel()
  text.value = ''
  draft.value = emptyQuery()
  emit('update:modelValue', emptyQuery())
}

const form = ref<HTMLFormElement | null>(null)

/** Put the focus on a control once the row it belongs to is in the DOM. */
async function focus(selector: string): Promise<void> {
  await nextTick()
  form.value?.querySelector<HTMLElement>(selector)?.focus()
}

// --- Text: a search box waits for a pause (lib/debounce.ts) ---
const text = ref(props.modelValue.text ?? '')
watch(
  () => props.modelValue.text,
  (value) => {
    text.value = value ?? ''
  },
)
function setText(value: string): void {
  const next = value.trim() === '' ? null : value
  if (next === draft.value.text) {
    return
  }
  edit((draft) => {
    draft.text = next
  })
}
const typing = debounce(setText, 300)

/** Searching is not waiting for a pause: what is typed goes out with the submit. */
function submit(): void {
  typing.cancel()
  setText(text.value)
  emit('submit')
}

// --- Types, layers, package ---
const types = computed({
  get: () => draft.value.element_types,
  set: (value: Query['element_types']) =>
    edit((draft) => {
      draft.element_types = value
    }),
})

function toggleLayer(layer: Query['layers'][number], on: boolean): void {
  edit((draft) => {
    draft.layers = on
      ? [...draft.layers.filter((l) => l !== layer), layer]
      : draft.layers.filter((l) => l !== layer)
  })
}

const packageChoice = computed({
  get: () => (draft.value.root_only ? ROOT : (draft.value.package_id ?? '')),
  set: (value: string) =>
    edit((draft) => {
      draft.root_only = value === ROOT
      draft.package_id = value && value !== ROOT ? value : null
    }),
})

// --- Attribute tests ---
function addProperty(): void {
  edit((draft) => {
    draft.properties.push({ key: '', op: 'eq', value: '' })
  })
  void focus(`[data-property-key="${draft.value.properties.length - 1}"]`)
}

function setProperty(index: number, patch: Partial<PropertyTest>): void {
  edit((draft) => {
    const test = draft.properties[index]
    if (!test) {
      return
    }
    Object.assign(test, patch)
    // `exists` and `absent` take no value; `eq` and `contains` need one.
    if (test.op === 'exists' || test.op === 'absent') {
      test.value = null
    } else if (test.value === null) {
      test.value = ''
    }
  })
}

function removeProperty(index: number): void {
  edit((draft) => {
    draft.properties.splice(index, 1)
  })
  // The row that held the focus is gone: the next one, else the button that adds.
  void focus(
    draft.value.properties.length > 0
      ? `[data-property-key="${Math.min(index, draft.value.properties.length - 1)}"]`
      : '[data-add-property]',
  )
}

// --- Relation tests ---
function addRelation(): void {
  const first = props.metamodel.byStrength.value[0]
  if (!first) {
    return
  }
  edit((draft) => {
    draft.relations.push({
      relationship_type: first.value,
      direction: 'any',
      present: true,
      other_type: null,
    })
  })
  void focus(`[data-relation-kind="${draft.value.relations.length - 1}"]`)
}

function setRelation(index: number, patch: Partial<RelationTest>): void {
  edit((draft) => {
    const test = draft.relations[index]
    if (test) {
      Object.assign(test, patch)
    }
  })
}

function removeRelation(index: number): void {
  edit((draft) => {
    draft.relations.splice(index, 1)
  })
  void focus(
    draft.value.relations.length > 0
      ? `[data-relation-kind="${Math.min(index, draft.value.relations.length - 1)}"]`
      : '[data-add-relation]',
  )
}

const sortedPackages = computed(() =>
  [...props.packages].sort((one, other) =>
    one.path.localeCompare(other.path, 'fr', { sensitivity: 'base' }),
  ),
)

function valueOf(event: Event): string {
  return (event.target as HTMLInputElement | HTMLSelectElement).value
}
</script>

<template>
  <form
    ref="form"
    class="builder"
    role="search"
    aria-label="Construire une recherche"
    @submit.prevent="submit"
  >
    <div class="row">
      <div class="field">
        <label for="q-text">Nom contient</label>
        <input id="q-text" v-model="text" type="search" autocomplete="off" @input="typing.call(valueOf($event))" />
      </div>

      <div class="field">
        <label for="q-types">Types d'éléments</label>
        <select id="q-types" v-model="types" multiple size="6">
          <optgroup
            v-for="group in metamodel.byLayer.value"
            :key="group.layer"
            :label="LAYER_LABELS[group.layer]"
          >
            <option v-for="type in group.types" :key="type.value" :value="type.value">
              {{ type.label }}
            </option>
          </optgroup>
        </select>
      </div>

      <fieldset class="field">
        <legend>Couches</legend>
        <label v-for="layer in metamodel.layers.value" :key="layer" class="check">
          <input
            type="checkbox"
            :checked="draft.layers.includes(layer)"
            @change="toggleLayer(layer, ($event.target as HTMLInputElement).checked)"
          />
          {{ LAYER_LABELS[layer] }}
        </label>
      </fieldset>

      <div class="field">
        <label for="q-package">Paquet</label>
        <select id="q-package" v-model="packageChoice">
          <option value="">Tous</option>
          <option :value="ROOT">Racine (sans paquet)</option>
          <option v-for="entry in sortedPackages" :key="entry.id" :value="entry.id">
            {{ entry.path }}
          </option>
        </select>
        <label v-if="draft.package_id" class="check">
          <input
            type="checkbox"
            :checked="draft.include_subpackages"
            @change="edit((draft) => { draft.include_subpackages = ($event.target as HTMLInputElement).checked })"
          />
          Inclure les sous-paquets
        </label>
      </div>
    </div>

    <fieldset class="tests">
      <legend>Attributs</legend>
      <div v-for="(test, index) in draft.properties" :key="index" class="test">
        <input
          :data-property-key="index"
          :aria-label="`Nom de la propriété ${index + 1}`"
          :value="test.key"
          placeholder="propriete"
          @input="setProperty(index, { key: valueOf($event) })"
        />
        <select
          :aria-label="`Opérateur de la propriété ${index + 1}`"
          :value="test.op"
          @change="setProperty(index, { op: valueOf($event) as PropertyTest['op'] })"
        >
          <option v-for="(label, op) in OPERATORS" :key="op" :value="op">{{ label }}</option>
        </select>
        <input
          v-if="test.op === 'eq' || test.op === 'contains'"
          :aria-label="`Valeur de la propriété ${index + 1}`"
          :value="test.value ?? ''"
          @input="setProperty(index, { value: valueOf($event) })"
        />
        <button
          type="button"
          class="secondary"
          :aria-label="`Retirer le test de propriété ${index + 1}`"
          @click="removeProperty(index)"
        >
          Retirer
        </button>
      </div>
      <button type="button" data-add-property @click="addProperty">Ajouter un test de propriété</button>
    </fieldset>

    <fieldset class="tests">
      <legend>Relations</legend>
      <div v-for="(test, index) in draft.relations" :key="index" class="test">
        <select
          :aria-label="`Présence de la relation ${index + 1}`"
          :value="test.present ? 'present' : 'absent'"
          @change="setRelation(index, { present: valueOf($event) === 'present' })"
        >
          <option value="present">a une relation</option>
          <option value="absent">n'a pas de relation</option>
        </select>
        <select
          :data-relation-kind="index"
          :aria-label="`Relation ${index + 1}`"
          :value="test.relationship_type"
          @change="setRelation(index, { relationship_type: valueOf($event) as RelationTest['relationship_type'] })"
        >
          <option v-for="type in metamodel.byStrength.value" :key="type.value" :value="type.value">
            {{ RELATIONSHIP_LABELS[type.value] }}
          </option>
        </select>
        <select
          :aria-label="`Sens de la relation ${index + 1}`"
          :value="test.direction"
          @change="setRelation(index, { direction: valueOf($event) as RelationTest['direction'] })"
        >
          <option v-for="(label, direction) in DIRECTIONS" :key="direction" :value="direction">
            {{ label }}
          </option>
        </select>
        <select
          :aria-label="`Type de l'autre bout de la relation ${index + 1}`"
          :value="test.other_type ?? ''"
          @change="setRelation(index, { other_type: (valueOf($event) || null) as RelationTest['other_type'] })"
        >
          <option value="">Peu importe</option>
          <optgroup
            v-for="group in metamodel.byLayer.value"
            :key="group.layer"
            :label="LAYER_LABELS[group.layer]"
          >
            <option v-for="type in group.types" :key="type.value" :value="type.value">
              {{ type.label }}
            </option>
          </optgroup>
        </select>
        <button
          type="button"
          class="secondary"
          :aria-label="`Retirer le test de relation ${index + 1}`"
          @click="removeRelation(index)"
        >
          Retirer
        </button>
      </div>
      <button
        type="button"
        data-add-relation
        :disabled="metamodel.byStrength.value.length === 0"
        @click="addRelation"
      >
        Ajouter un test de relation
      </button>
    </fieldset>

    <div class="row">
      <div class="field">
        <label for="q-order">Trier par</label>
        <select
          id="q-order"
          :value="draft.order"
          @change="edit((draft) => { draft.order = valueOf($event) as Query['order'] })"
        >
          <option v-for="(label, order) in ORDERS" :key="order" :value="order">{{ label }}</option>
        </select>
      </div>
      <button type="submit">Rechercher</button>
      <button type="button" class="secondary" @click="reset">
        Réinitialiser
      </button>
    </div>
  </form>
</template>

<style scoped>
.builder {
  display: grid;
  gap: 0.8rem;
}
.row {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  gap: 0.8rem;
}
.field {
  display: grid;
  gap: 0.25rem;
  border: none;
  padding: 0;
  margin: 0;
}
label,
legend {
  font-size: 0.85rem;
  font-weight: 600;
}
.check {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-weight: 400;
}
.tests {
  display: grid;
  gap: 0.5rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.6rem;
}
.test {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}
input,
select {
  font: inherit;
  padding: 0.35rem 0.55rem;
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
</style>
