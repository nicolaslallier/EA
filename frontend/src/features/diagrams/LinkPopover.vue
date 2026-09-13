<script setup lang="ts">
// The form a link drawn on the canvas opens: which relationship joins the two
// boxes, in the direction they were drawn.
//
// It does not know what ArchiMate permits; it asks `/metamodel/relationships`
// for the pair — through the relations panel's own composable — and offers
// only the answer.
import { computed, ref, watch } from 'vue'

import { messageOf } from '../../lib/api'
import { ACCESS_LABELS, RELATIONSHIP_LABELS } from '../relationships/labels'
import type { AccessType, RelationshipType } from '../relationships/useElementRelationships'
import { useElementRelationships } from '../relationships/useElementRelationships'
import type { ElementRead, RelationshipCreate } from './useDiagram'

const props = defineProps<{
  source: ElementRead
  target: ElementRead
  /** Creates the relationship; a refusal is thrown and shown here. */
  connect: (payload: RelationshipCreate) => Promise<void>
}>()

const emit = defineEmits<{ done: []; cancel: [] }>()

const relations = useElementRelationships()
const relationshipType = ref<RelationshipType | ''>('')
const linkName = ref('')
const accessType = ref<AccessType>('access')
const directed = ref(false)
const failure = ref('')
const busy = ref(false)
const answered = ref(false)

const nothingPermitted = computed(
  () => answered.value && !relations.lookupError.value && relations.permitted.value.length === 0,
)

watch(
  () => [props.source.id, props.target.id],
  async () => {
    relationshipType.value = ''
    failure.value = ''
    answered.value = false
    relations.clearPermitted()
    await relations.loadPermitted(props.source.element_type, props.target.element_type)
    answered.value = true
  },
  { immediate: true },
)

async function submit(): Promise<void> {
  if (relationshipType.value === '') {
    return
  }
  busy.value = true
  failure.value = ''
  try {
    await props.connect({
      relationship_type: relationshipType.value,
      source_id: props.source.id,
      target_id: props.target.id,
      name: linkName.value.trim(),
      // Both qualifiers mean something on one type only, and the API refuses
      // them elsewhere — sent only where they apply, as the relations panel does.
      ...(relationshipType.value === 'access' ? { access_type: accessType.value } : {}),
      ...(relationshipType.value === 'association' ? { directed: directed.value } : {}),
    })
    emit('done')
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <form class="link" aria-label="Relier deux éléments" @submit.prevent="submit">
    <p class="link__pair">
      <strong>{{ source.name }}</strong> → <strong>{{ target.name }}</strong>
    </p>

    <p v-if="failure || relations.lookupError.value" class="banner" role="alert">
      {{ failure || relations.lookupError.value }}
    </p>

    <div class="field">
      <label for="link-type">Type de relation</label>
      <select id="link-type" v-model="relationshipType"
              :disabled="relations.permitted.value.length === 0">
        <option value="">Choisis une relation…</option>
        <option v-for="permitted in relations.permitted.value" :key="permitted" :value="permitted">
          {{ RELATIONSHIP_LABELS[permitted] }}
        </option>
      </select>
      <p v-if="nothingPermitted" class="hint">
        Aucune relation n'est permise dans ce sens — tire le lien depuis l'autre élément.
      </p>
    </div>

    <div v-if="relationshipType === 'access'" class="field">
      <label for="link-access">Mode d'accès</label>
      <select id="link-access" v-model="accessType">
        <option v-for="(label, value) in ACCESS_LABELS" :key="value" :value="value">
          {{ label }}
        </option>
      </select>
    </div>

    <div v-if="relationshipType === 'association'" class="field field--inline">
      <input id="link-directed" v-model="directed" type="checkbox" />
      <label for="link-directed">Association orientée</label>
    </div>

    <div class="field">
      <label for="link-name">Intitulé (facultatif)</label>
      <input id="link-name" v-model="linkName" maxlength="200" autocomplete="off" />
    </div>

    <div class="actions">
      <button type="submit" :disabled="relationshipType === '' || busy">Relier</button>
      <button type="button" class="secondary" @click="emit('cancel')">Annuler</button>
    </div>
  </form>
</template>

<style scoped>
.link {
  display: grid;
  gap: 0.6rem;
  padding: 0.8rem;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.link__pair {
  margin: 0;
}
.field {
  display: grid;
  gap: 0.25rem;
}
.field--inline {
  grid-template-columns: auto 1fr;
  align-items: center;
  gap: 0.5rem;
}
label {
  font-weight: 600;
  font-size: 0.85rem;
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
.actions {
  display: flex;
  gap: 0.5rem;
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
.hint {
  margin: 0;
  font-size: 0.85rem;
  opacity: 0.75;
}
.banner {
  margin: 0;
  padding: 0.5rem 0.8rem;
  border: 1px solid #c62828;
  border-radius: 6px;
  color: #c62828;
}
</style>
