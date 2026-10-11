<script setup lang="ts">
// The form of one stereotype: its name, the type it extends, its colour and
// icon, and the properties it expects. It checks nothing the API checks — a
// dangerous pattern or a reserved key comes back as the API's own refusal.
import { ref } from 'vue'

import type { IconKey, StereotypeRead, StereotypeWrite } from './useProfiles'

const props = defineProps<{
  stereotype?: StereotypeRead
  /** The base types, as `/metamodel` offers them. */
  types: { value: string; label: string }[]
  failure: string
}>()
const emit = defineEmits<{ save: [body: StereotypeWrite]; cancel: [] }>()

/** The closed set of icons the SPA ships (the API validates the same set). */
const ICONS: IconKey[] = [
  'none', 'database', 'server', 'cloud', 'user', 'lock', 'document', 'gear', 'queue', 'api',
]
type Draft = StereotypeWrite['properties'][number]

const name = ref(props.stereotype?.name ?? '')
const baseType = ref<string>(props.stereotype?.base_type ?? '')
const colour = ref(props.stereotype?.colour ?? '')
const icon = ref<IconKey>(props.stereotype?.icon_key ?? 'none')
const properties = ref<Draft[]>(props.stereotype?.properties.map((p) => ({ ...p })) ?? [])

function addProperty(): void {
  properties.value.push({
    key: '',
    label: '',
    required: false,
    kind: 'free',
    allowed_values: [],
    template: null,
    help: '',
  })
}

function save(): void {
  emit('save', {
    name: name.value.trim(),
    base_type: baseType.value as StereotypeWrite['base_type'],
    colour: colour.value || null,
    icon_key: icon.value,
    properties: properties.value.map((p) => ({
      ...p,
      allowed_values: p.kind === 'enum' ? p.allowed_values : [],
      template: p.kind === 'template' ? p.template : null,
    })),
  })
}

function valuesOf(p: Draft): string {
  return p.allowed_values.join('\n')
}
function setValues(p: Draft, text: string): void {
  p.allowed_values = text.split('\n').map((v) => v.trim()).filter(Boolean)
}
</script>

<template>
  <form class="stereotype-form" aria-label="Stéréotype" @submit.prevent="save">
    <label for="st-name">Nom du stéréotype</label>
    <input id="st-name" v-model="name" maxlength="100" autocomplete="off" />
    <label for="st-type">Type de base</label>
    <select id="st-type" v-model="baseType">
      <option value="" disabled>Choisir…</option>
      <option v-for="type in types" :key="type.value" :value="type.value">{{ type.label }}</option>
    </select>
    <label for="st-colour">Couleur</label>
    <input id="st-colour" v-model="colour" type="color" />
    <label for="st-icon">Icône</label>
    <select id="st-icon" v-model="icon">
      <option v-for="key in ICONS" :key="key" :value="key">{{ key }}</option>
    </select>

    <fieldset v-for="(p, index) in properties" :key="index" class="property">
      <legend>Propriété {{ index + 1 }}</legend>
      <label :for="`p-key-${index}`">Clé</label>
      <input :id="`p-key-${index}`" v-model="p.key" maxlength="63" autocomplete="off" />
      <label :for="`p-label-${index}`">Libellé</label>
      <input :id="`p-label-${index}`" v-model="p.label" maxlength="100" />
      <label><input v-model="p.required" type="checkbox" /> Obligatoire</label>
      <label :for="`p-kind-${index}`">Forme</label>
      <select :id="`p-kind-${index}`" v-model="p.kind">
        <option value="free">Libre</option>
        <option value="enum">Liste de valeurs</option>
        <option value="template">Gabarit</option>
      </select>
      <template v-if="p.kind === 'enum'">
        <label :for="`p-values-${index}`">Valeurs permises (une par ligne)</label>
        <textarea :id="`p-values-${index}`" :value="valuesOf(p)" rows="3" @input="setValues(p, ($event.target as HTMLTextAreaElement).value)" />
      </template>
      <template v-if="p.kind === 'template'">
        <label :for="`p-template-${index}`">Gabarit</label>
        <input :id="`p-template-${index}`" v-model="p.template" maxlength="64" />
        <p class="hint">* = n'importe quelle suite, ? = un caractère, # = un chiffre.</p>
      </template>
      <label :for="`p-help-${index}`">Aide</label>
      <input :id="`p-help-${index}`" v-model="p.help" maxlength="300" />
      <button type="button" class="secondary" @click="properties.splice(index, 1)">Retirer la propriété</button>
    </fieldset>

    <button type="button" class="secondary" @click="addProperty">Ajouter une propriété</button>
    <p v-if="failure" class="banner banner--error" role="alert">{{ failure }}</p>
    <div class="actions">
      <button type="submit">Enregistrer le stéréotype</button>
      <button type="button" class="secondary" @click="emit('cancel')">Renoncer</button>
    </div>
  </form>
</template>

<style scoped>
.stereotype-form,
.property {
  display: grid;
  gap: 0.4rem;
}
.property {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.6rem;
}
.actions {
  display: flex;
  gap: 0.5rem;
}
</style>
