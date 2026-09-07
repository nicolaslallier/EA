<script setup lang="ts">
// The relations of one element: what it is linked to, and the form that adds a
// link.
//
// Every rule stays server-side. The form does not know what ArchiMate permits;
// it asks `/metamodel/relationships` for the pair being built and offers only
// the answer, so the user never picks a relationship the API would refuse.
import { computed, onMounted, ref, watch } from 'vue'

import { messageOf } from '../../lib/api'
import type { ElementRead } from '../elements/useElementCatalogue'
import { ACCESS_LABELS, RELATIONSHIP_LABELS, verbOf } from './labels'
import type { AccessType, RelationshipType } from './useElementRelationships'
import { useElementRelationships } from './useElementRelationships'

const props = defineProps<{ element: ElementRead }>()
const emit = defineEmits<{ close: [] }>()

/** Which way round the new link runs, seen from the element on screen. */
type Direction = 'outgoing' | 'incoming'

const relations = useElementRelationships()

const direction = ref<Direction>('outgoing')
const otherId = ref('')
const relationshipType = ref<RelationshipType | ''>('')
const linkName = ref('')
const accessType = ref<AccessType>('access')
const directed = ref(false)
const failure = ref('')
const busy = ref(false)
/** The link whose removal is waiting for a confirmation. */
const confirming = ref<string | null>(null)

const other = computed(() =>
  relations.candidates.value.find((candidate) => candidate.id === otherId.value),
)

/** The pair the metamodel is asked about, in the order the link will be stored. */
const pair = computed(() => {
  if (!other.value) {
    return null
  }
  return direction.value === 'outgoing'
    ? { source: props.element, target: other.value }
    : { source: other.value, target: props.element }
})

const nothingPermitted = computed(
  () => Boolean(other.value) && relations.permitted.value.length === 0,
)

const canSubmit = computed(() => Boolean(pair.value) && relationshipType.value !== '' && !busy.value)

onMounted(async () => {
  await relations.open(props.element)
  await loadCandidates()
})

async function loadCandidates(term = ''): Promise<void> {
  try {
    await relations.searchCandidates(term)
  } catch (caught) {
    failure.value = messageOf(caught)
  }
}

// A changed pair invalidates the choice that was legal for the previous one, so
// the type is cleared rather than carried over and refused on submit.
watch([otherId, direction], async () => {
  relationshipType.value = ''
  relations.permitted.value = []
  const asked = pair.value
  if (!asked) {
    return
  }
  failure.value = ''
  try {
    await relations.loadPermitted(asked.source.element_type, asked.target.element_type)
  } catch (caught) {
    failure.value = messageOf(caught)
  }
})

async function onSearch(event: Event): Promise<void> {
  await loadCandidates((event.target as HTMLInputElement).value)
}

async function onSubmit(): Promise<void> {
  const asked = pair.value
  if (!asked || relationshipType.value === '') {
    return
  }
  busy.value = true
  failure.value = ''
  try {
    await relations.connect({
      relationship_type: relationshipType.value,
      source_id: asked.source.id,
      target_id: asked.target.id,
      name: linkName.value.trim(),
      // Both qualifiers are only meaningful on one relationship type, and the
      // API rejects them elsewhere, so they are sent only where they apply.
      ...(relationshipType.value === 'access' ? { access_type: accessType.value } : {}),
      ...(relationshipType.value === 'association' ? { directed: directed.value } : {}),
    })
    otherId.value = ''
    relationshipType.value = ''
    linkName.value = ''
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

async function confirmDelete(): Promise<void> {
  const id = confirming.value
  if (!id) {
    return
  }
  busy.value = true
  try {
    await relations.disconnect(id)
    confirming.value = null
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="relations" aria-label="Relations de l'élément">
    <header class="relations__header">
      <h3>Relations de « {{ element.name }} »</h3>
      <button type="button" class="secondary" @click="emit('close')">Fermer</button>
    </header>

    <p v-if="relations.status.value === 'error'" class="banner banner--error" role="alert">
      {{ relations.error.value }}
    </p>

    <p v-if="relations.status.value === 'loading'" class="hint">Chargement des relations…</p>

    <p
      v-else-if="relations.links.value.length === 0 && relations.status.value === 'ready'"
      class="hint"
    >
      « {{ element.name }} » n'est encore associé à rien.
    </p>

    <table v-else-if="relations.links.value.length > 0">
      <caption class="sr-only">Relations de {{ element.name }}</caption>
      <thead>
        <tr>
          <th scope="col">Source</th>
          <th scope="col">Relation</th>
          <th scope="col">Cible</th>
          <th scope="col">Intitulé</th>
          <th scope="col">Actions</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="link in relations.links.value" :key="link.id">
          <td>{{ relations.nameOf(link.source_id) }}</td>
          <td>{{ verbOf(link.relationship_type, link.access_type) }}</td>
          <td>{{ relations.nameOf(link.target_id) }}</td>
          <td>{{ link.name }}</td>
          <td>
            <button type="button" class="secondary" @click="confirming = link.id">
              Dissocier
            </button>
          </td>
        </tr>
      </tbody>
    </table>

    <p v-if="confirming" class="banner banner--confirm" role="alert">
      Retirer cette relation ? Les deux éléments sont conservés.
      <button type="button" :disabled="busy" @click="confirmDelete">Confirmer</button>
      <button type="button" class="secondary" @click="confirming = null">Renoncer</button>
    </p>

    <form class="form" aria-label="Associer un élément" @submit.prevent="onSubmit">
      <p v-if="failure" class="banner banner--error" role="alert">{{ failure }}</p>

      <div class="field">
        <label for="relation-direction">Sens</label>
        <select id="relation-direction" v-model="direction">
          <option value="outgoing">{{ element.name }} → l'autre élément</option>
          <option value="incoming">l'autre élément → {{ element.name }}</option>
        </select>
      </div>

      <div class="field">
        <label for="relation-search">Rechercher un élément</label>
        <input
          id="relation-search"
          type="search"
          placeholder="Un fragment de nom"
          @input="onSearch"
        />
      </div>

      <div class="field">
        <label for="relation-other">Autre élément</label>
        <select id="relation-other" v-model="otherId">
          <option value="">Choisis un élément…</option>
          <option v-for="candidate in relations.candidates.value" :key="candidate.id"
                  :value="candidate.id">
            {{ candidate.name }}
          </option>
        </select>
      </div>

      <div class="field">
        <label for="relation-type">Type de relation</label>
        <select id="relation-type" v-model="relationshipType"
                :disabled="relations.permitted.value.length === 0">
          <option value="">Choisis une relation…</option>
          <option v-for="permitted in relations.permitted.value" :key="permitted" :value="permitted">
            {{ RELATIONSHIP_LABELS[permitted] }}
          </option>
        </select>
        <p v-if="nothingPermitted" class="hint">
          Aucune relation n'est permise entre ces deux types dans ce sens — essaie l'autre sens.
        </p>
      </div>

      <div v-if="relationshipType === 'access'" class="field">
        <label for="relation-access">Mode d'accès</label>
        <select id="relation-access" v-model="accessType">
          <option v-for="(label, value) in ACCESS_LABELS" :key="value" :value="value">
            {{ label }}
          </option>
        </select>
      </div>

      <div v-if="relationshipType === 'association'" class="field field--inline">
        <input id="relation-directed" v-model="directed" type="checkbox" />
        <label for="relation-directed">Association orientée</label>
      </div>

      <div class="field">
        <label for="relation-name">Intitulé (facultatif)</label>
        <input id="relation-name" v-model="linkName" maxlength="200" autocomplete="off" />
      </div>

      <div class="actions">
        <button type="submit" :disabled="!canSubmit">Associer</button>
      </div>
    </form>
  </section>
</template>

<style scoped>
.relations {
  display: grid;
  gap: 0.9rem;
  padding: 1rem;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.relations__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
}
.relations__header h3 {
  margin: 0;
}
.form {
  display: grid;
  gap: 0.7rem;
  border-top: 1px solid var(--border);
  padding-top: 0.9rem;
}
.field {
  display: grid;
  gap: 0.3rem;
}
.field--inline {
  grid-template-columns: auto 1fr;
  align-items: center;
  gap: 0.5rem;
}
label {
  font-weight: 600;
  font-size: 0.9rem;
}
input,
select {
  font: inherit;
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: inherit;
  color: inherit;
}
input[type='checkbox'] {
  width: auto;
}
button {
  font: inherit;
  padding: 0.45rem 0.9rem;
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
table {
  width: 100%;
  border-collapse: collapse;
}
th,
td {
  text-align: left;
  padding: 0.5rem 0.6rem;
  border-bottom: 1px solid var(--border);
}
.hint {
  margin: 0;
  font-size: 0.85rem;
  opacity: 0.75;
}
.banner {
  margin: 0;
  padding: 0.6rem 0.9rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  display: flex;
  align-items: center;
  gap: 0.6rem;
}
.banner--error {
  border-color: #c62828;
  color: #c62828;
}
.banner--confirm {
  border-color: #ef6c00;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
</style>
