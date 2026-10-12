<script setup lang="ts">
// Fill the slots of a template: for each, pick an element of its type, create
// one, or leave it out. The type filter is the slot's own - the API refuses a
// wrong one anyway. A slot that requires a stereotype cannot be filled by
// creating (a new element would not carry it). Relationships are off unless
// the box is ticked.
import { reactive, ref } from 'vue'

import { debounce } from '../../lib/debounce'
import type { ElementRead } from './useDiagram'
import type { DiagramTemplateRead, InstantiateRequest } from './useTemplates'

type Binding = InstantiateRequest['bindings'][number]

const props = defineProps<{
  template: DiagramTemplateRead
  search: (term: string, elementType: string) => Promise<ElementRead[]>
  typeLabel: (value: string) => string
  failure: string
}>()
const emit = defineEmits<{ submit: [payload: InstantiateRequest]; cancel: [] }>()

type Choice = {
  term: string
  results: ElementRead[]
  picked: ElementRead | null
  create: boolean
  name: string
}
const choices = reactive<Record<string, Choice>>(
  Object.fromEntries(
    props.template.body.slots.map((slot) => [
      slot.key,
      { term: '', results: [], picked: null, create: false, name: '' },
    ]),
  ),
)
const viewName = ref('')
const createRelationships = ref(false)

// One pause per slot: typing in the second must not swallow the first one's lookup.
const lookups = new Map<string, ReturnType<typeof debounce<[string, string]>>>()

function lookupOf(key: string) {
  let lookup = lookups.get(key)
  if (!lookup) {
    lookup = debounce(async (term: string, type: string) => {
      choices[key].results = term.trim() ? await props.search(term.trim(), type) : []
    }, 250)
    lookups.set(key, lookup)
  }
  return lookup
}

function typed(key: string, type: string, term: string): void {
  choices[key].term = term
  choices[key].picked = null
  lookupOf(key).call(term, type)
}

function pick(key: string, element: ElementRead): void {
  choices[key].picked = element
  choices[key].results = []
  choices[key].term = element.name
}

function submit(): void {
  const bindings = props.template.body.slots.flatMap((slot): Binding[] => {
    const choice = choices[slot.key]
    if (choice.create && choice.name.trim()) {
      return [{ slot_key: slot.key, element_id: null, create: { name: choice.name.trim() } }]
    }
    return choice.picked ? [{ slot_key: slot.key, element_id: choice.picked.id, create: null }] : []
  })
  emit('submit', {
    name: viewName.value.trim(),
    bindings,
    create_relationships: createRelationships.value,
  })
}
</script>

<template>
  <form class="wizard" aria-label="Instancier un modèle" @submit.prevent="submit">
    <h3>{{ template.name }}</h3>
    <label for="wizard-name">Nom de la vue</label>
    <input id="wizard-name" v-model="viewName" maxlength="200" autocomplete="off" />

    <fieldset v-for="slot in template.body.slots" :key="slot.key" class="slot">
      <legend>{{ slot.label }} — {{ typeLabel(slot.element_type) }}</legend>
      <p v-if="slot.stereotype" class="hint">
        Doit porter «{{ slot.stereotype }}» : choisis un élément existant.
      </p>
      <label :for="`search-${slot.key}`">{{ slot.label }} — chercher un élément</label>
      <input
        :id="`search-${slot.key}`"
        :value="choices[slot.key].term"
        :disabled="choices[slot.key].create"
        autocomplete="off"
        @input="typed(slot.key, slot.element_type, ($event.target as HTMLInputElement).value)"
      />
      <ul v-if="choices[slot.key].results.length > 0">
        <li v-for="element in choices[slot.key].results" :key="element.id">
          <button type="button" class="secondary" @click="pick(slot.key, element)">
            {{ element.name }}
          </button>
        </li>
      </ul>
      <label>
        <input
          v-model="choices[slot.key].create"
          type="checkbox"
          :disabled="Boolean(slot.stereotype)"
          :aria-label="`${slot.label} — créer l'élément`"
        />
        Créer l'élément
      </label>
      <template v-if="choices[slot.key].create">
        <label :for="`new-${slot.key}`">{{ slot.label }} — nom du nouvel élément</label>
        <input :id="`new-${slot.key}`" v-model="choices[slot.key].name" maxlength="200" />
      </template>
    </fieldset>

    <label>
      <input v-model="createRelationships" type="checkbox" />
      Créer aussi les relations du modèle
    </label>
    <p class="hint">
      Les relations du modèle ne sont créées dans le catalogue que si cette case est cochée.
    </p>

    <p v-if="failure" class="banner banner--error" role="alert">{{ failure }}</p>
    <div class="actions">
      <button type="submit" :disabled="!viewName.trim()">Créer la vue</button>
      <button type="button" class="secondary" @click="emit('cancel')">Renoncer</button>
    </div>
  </form>
</template>

<style scoped>
.wizard,
.slot {
  display: grid;
  gap: 0.4rem;
}
.slot {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.6rem;
}
.actions {
  display: flex;
  gap: 0.5rem;
}
</style>
