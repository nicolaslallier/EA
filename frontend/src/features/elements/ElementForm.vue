<script setup lang="ts">
// Create or edit one element. The form owns its draft and validates what it
// can locally, but the API stays the authority: `failure` displays whatever it
// refused, and the draft is kept so nothing typed is lost.
import { computed, ref, watch } from 'vue'

import type { LayerGroup } from '../metamodel/useMetamodel'
import { LAYER_LABELS } from '../metamodel/useMetamodel'
import type { ElementCreate, ElementRead, ElementType, ElementUpdate } from './useElementCatalogue'

const props = defineProps<{
  layerGroups: LayerGroup[]
  /** The element being edited, or nothing when creating one. */
  element?: ElementRead | null
  /** What the API refused last time, ready to display. */
  failure?: string
  busy?: boolean
}>()

const emit = defineEmits<{
  submit: [payload: ElementCreate | ElementUpdate]
  cancel: []
}>()

// A user-defined attribute becomes a graph property, so its name has to be a
// plain identifier — the same rule `domain/model.py` enforces server-side.
// Checking it here turns a 422 into a message next to the offending field.
const PROPERTY_KEY = /^[A-Za-z][A-Za-z0-9_]{0,62}$/

type PropertyRow = { key: string; value: string }

const isEditing = computed(() => Boolean(props.element))

const elementType = ref<ElementType>(props.element?.element_type ?? 'application_component')
const name = ref(props.element?.name ?? '')
const description = ref(props.element?.description ?? '')
const documentation = ref(props.element?.documentation ?? '')
const properties = ref<PropertyRow[]>(rowsOf(props.element))
const invalid = ref('')

function rowsOf(element?: ElementRead | null): PropertyRow[] {
  return Object.entries(element?.properties ?? {}).map(([key, value]) => ({ key, value }))
}

// The parent reuses one form for "new" and for "edit that row", so a changed
// `element` has to reset the draft rather than leave the previous one behind.
watch(
  () => props.element,
  (element) => {
    elementType.value = element?.element_type ?? 'application_component'
    name.value = element?.name ?? ''
    description.value = element?.description ?? ''
    documentation.value = element?.documentation ?? ''
    properties.value = rowsOf(element)
    invalid.value = ''
  },
)

function addProperty(): void {
  properties.value.push({ key: '', value: '' })
}

function removeProperty(index: number): void {
  properties.value.splice(index, 1)
}

function collectProperties(): Record<string, string> | null {
  const collected: Record<string, string> = {}
  for (const row of properties.value) {
    const key = row.key.trim()
    if (!key) {
      continue
    }
    if (!PROPERTY_KEY.test(key)) {
      invalid.value = `« ${key} » n'est pas un identifiant simple : lettres, chiffres et
        soulignés, commençant par une lettre.`
      return null
    }
    collected[key] = row.value
  }
  return collected
}

function onSubmit(): void {
  invalid.value = ''
  if (!name.value.trim()) {
    invalid.value = 'Le nom est obligatoire.'
    return
  }
  const collected = collectProperties()
  if (collected === null) {
    return
  }

  const common = {
    name: name.value.trim(),
    description: description.value.trim(),
    documentation: documentation.value.trim(),
    properties: collected,
  }
  // An update deliberately carries no type: retyping an element could
  // invalidate relationships that already exist, so the API refuses it.
  emit('submit', isEditing.value ? common : { ...common, element_type: elementType.value })
}
</script>

<template>
  <form class="form" role="form" :aria-label="isEditing ? 'Modifier l’élément' : 'Nouvel élément'"
        @submit.prevent="onSubmit">
    <p v-if="failure" class="banner banner--error" role="alert">{{ failure }}</p>
    <p v-else-if="invalid" class="banner banner--error" role="alert">{{ invalid }}</p>

    <div class="field">
      <label for="element-type">Type</label>
      <select id="element-type" v-model="elementType" :disabled="isEditing">
        <optgroup v-for="group in layerGroups" :key="group.layer" :label="LAYER_LABELS[group.layer]">
          <option v-for="type in group.types" :key="type.value" :value="type.value">
            {{ type.label }}
          </option>
        </optgroup>
      </select>
      <p v-if="isEditing" class="hint">
        Le type d'un élément ne se change pas : les relations déjà stockées en dépendent.
      </p>
    </div>

    <div class="field">
      <label for="element-name">Nom</label>
      <input id="element-name" v-model="name" autocomplete="off" maxlength="200" />
    </div>

    <div class="field">
      <label for="element-description">Description</label>
      <input id="element-description" v-model="description" maxlength="2000" />
    </div>

    <div class="field">
      <label for="element-documentation">Documentation</label>
      <textarea id="element-documentation" v-model="documentation" rows="3" maxlength="20000" />
    </div>

    <fieldset class="properties">
      <legend>Attributs</legend>
      <div v-for="(row, index) in properties" :key="index" class="property">
        <input
          v-model="row.key"
          :aria-label="`Clé de l'attribut ${index + 1}`"
          placeholder="owner"
        />
        <input
          v-model="row.value"
          :aria-label="`Valeur de l'attribut ${index + 1}`"
          placeholder="finance"
        />
        <button type="button" :aria-label="`Retirer l'attribut ${index + 1}`" @click="removeProperty(index)">
          ×
        </button>
      </div>
      <button type="button" class="secondary" @click="addProperty">Ajouter un attribut</button>
    </fieldset>

    <div class="actions">
      <button type="submit" :disabled="busy">
        {{ isEditing ? 'Enregistrer' : 'Créer' }}
      </button>
      <button type="button" class="secondary" @click="emit('cancel')">Annuler</button>
    </div>
  </form>
</template>

<style scoped>
.form {
  display: grid;
  gap: 0.9rem;
  padding: 1rem;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.field {
  display: grid;
  gap: 0.3rem;
}
label,
legend {
  font-weight: 600;
  font-size: 0.9rem;
}
input,
select,
textarea {
  font: inherit;
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: inherit;
  color: inherit;
}
.hint {
  margin: 0;
  font-size: 0.85rem;
  opacity: 0.75;
}
.properties {
  display: grid;
  gap: 0.5rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.7rem;
}
.property {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
  gap: 0.4rem;
}
.actions {
  display: flex;
  gap: 0.5rem;
}
button {
  font: inherit;
  padding: 0.45rem 0.9rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
}
.secondary {
  background: transparent;
}
.banner--error {
  margin: 0;
  padding: 0.6rem 0.9rem;
  border: 1px solid #c62828;
  border-radius: 6px;
  color: #c62828;
}
</style>
