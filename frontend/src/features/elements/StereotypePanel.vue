<script setup lang="ts">
// The stereotypes an element carries, and the properties they expect.
//
// It shows the rules it is given and applies none: an enumeration is a list,
// a template a hint, a required property a star. A value that breaks the
// declared format comes back as the API's refusal. The values live in the
// element's own `properties` (docs/adr/0064), and the API's
// PATCH replaces that whole map — so a save sends all of it.
import { computed, onMounted, reactive, ref, watch } from 'vue'

import { api, messageOf, unwrap } from '../../lib/api'
import { useMe } from '../../lib/me'
import { stereotypeLabel } from '../../lib/stereotypeRender'
import { useStereotypes } from '../profiles/useStereotypes'
import type { ElementRead } from './useElementCatalogue'

const props = defineProps<{ element: ElementRead }>()
const emit = defineEmits<{ changed: [] }>()

const { canWrite } = useMe()
const catalogue = useStereotypes()
const chosen = ref('')
const failure = ref('')
const values = reactive<Record<string, string>>({})

onMounted(() => catalogue.load())

const carried = computed(() =>
  catalogue.all.value.filter((stereotype) => props.element.stereotypes.some((ref) => ref.id === stereotype.id)),
)
const offered = computed(() =>
  catalogue
    .forType(props.element.element_type)
    .filter((stereotype) => !props.element.stereotypes.some((ref) => ref.id === stereotype.id)),
)
const declared = computed(() => carried.value.flatMap((stereotype) => stereotype.properties))

watch(
  () => [props.element.id, props.element.properties] as const,
  () => {
    for (const key of Object.keys(values)) {
      delete values[key]
    }
    for (const prop of declared.value) {
      values[prop.key] = props.element.properties[prop.key] ?? ''
    }
  },
  { immediate: true },
)
watch(declared, () => {
  for (const prop of declared.value) {
    values[prop.key] ??= props.element.properties[prop.key] ?? ''
  }
})

async function apply(ids: string[]): Promise<void> {
  failure.value = ''
  try {
    unwrap(
      await api.PUT('/elements/{element_id}/stereotypes', {
        params: { path: { element_id: props.element.id } },
        body: { stereotype_ids: ids },
      }),
    )
    chosen.value = ''
    emit('changed')
  } catch (caught) {
    failure.value = messageOf(caught)
  }
}

async function saveValues(): Promise<void> {
  failure.value = ''
  // The element's own attributes stay; the declared ones are laid over them,
  // and a blank declared value is a value not set.
  const next: Record<string, string> = { ...props.element.properties }
  for (const prop of declared.value) {
    const value = values[prop.key]?.trim() ?? ''
    if (value) {
      next[prop.key] = value
    } else {
      delete next[prop.key]
    }
  }
  try {
    unwrap(
      await api.PATCH('/elements/{element_id}', {
        params: { path: { element_id: props.element.id } },
        body: { properties: next },
      }),
    )
    emit('changed')
  } catch (caught) {
    failure.value = messageOf(caught)
  }
}
</script>

<template>
  <section class="stereotypes" aria-label="Stéréotypes">
    <h4>Stéréotypes</h4>
    <p v-if="element.stereotypes.length === 0" class="empty">Aucun stéréotype.</p>
    <ul v-else class="chips">
      <li v-for="tag in element.stereotypes" :key="tag.id">
        <span>{{ stereotypeLabel([tag]) }}</span>
        <button
          v-if="canWrite"
          type="button"
          class="secondary"
          :aria-label="`Retirer ${tag.name}`"
          @click="apply(element.stereotypes.filter((other) => other.id !== tag.id).map((other) => other.id))"
        >
          ×
        </button>
      </li>
    </ul>

    <div v-if="canWrite && offered.length > 0" class="add">
      <label for="stereotype-choice">Ajouter un stéréotype</label>
      <select id="stereotype-choice" v-model="chosen">
        <option value="" disabled>Choisir…</option>
        <option v-for="stereotype in offered" :key="stereotype.id" :value="stereotype.id">
          {{ stereotype.name }}
        </option>
      </select>
      <button
        type="button"
        :disabled="!chosen"
        @click="apply([...element.stereotypes.map((ref) => ref.id), chosen])"
      >
        Appliquer
      </button>
    </div>

    <form v-if="declared.length > 0" class="properties" @submit.prevent="saveValues">
      <div v-for="prop in declared" :key="prop.key" class="property">
        <label :for="`prop-${prop.key}`">
          {{ prop.label }}<span v-if="prop.required" class="required" title="Obligatoire">*</span>
        </label>
        <select v-if="prop.kind === 'enum'" :id="`prop-${prop.key}`" v-model="values[prop.key]" :disabled="!canWrite">
          <option value="">—</option>
          <option v-for="allowed in prop.allowed_values" :key="allowed" :value="allowed">{{ allowed }}</option>
        </select>
        <input
          v-else
          :id="`prop-${prop.key}`"
          v-model="values[prop.key]"
          :disabled="!canWrite"
          maxlength="500"
          :placeholder="prop.kind === 'template' ? `Forme attendue : ${prop.template}` : ''"
        />
        <small v-if="prop.help" class="hint">{{ prop.help }}</small>
      </div>
      <button v-if="canWrite" type="submit">Enregistrer les propriétés</button>
    </form>

    <p v-if="failure" class="banner banner--error" role="alert">{{ failure }}</p>
  </section>
</template>

<style scoped>
.stereotypes,
.properties,
.property {
  display: grid;
  gap: 0.4rem;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  padding: 0;
  list-style: none;
}
.required {
  color: #b3261e;
  margin-left: 0.2rem;
}
</style>
