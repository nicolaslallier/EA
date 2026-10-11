<script setup lang="ts">
// One element, in full: the fields a table row has no room for — the
// documentation, the user-defined attributes, the identity and the dates.
//
// It is given the element rather than fetching it: the read belongs to the
// screen that answers the URL, and this component only has to draw whatever it
// is handed, whichever screen that is.
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import ApprovalBanner from './ApprovalBanner.vue'
import ElementHistory from '../history/ElementHistory.vue'
import { ASPECT_LABELS, LAYER_LABELS } from '../metamodel/useMetamodel'
import type { ElementRead } from './useElementCatalogue'

const props = defineProps<{
  element: ElementRead
  typeLabel: string
  packagePath?: string
}>()
defineEmits<{ close: [] }>()

// A transition answers with the element as it is now, and a stale approval with
// the element re-read: the sheet follows that copy rather than the prop.
const shown = ref(props.element)
watch(
  () => props.element,
  (next) => {
    shown.value = next
  },
)

const attributes = computed(() => Object.entries(shown.value.properties))

/** ISO instants are for machines; a date is what the reader needs. */
function day(iso: string): string {
  return iso.slice(0, 10)
}
</script>

<template>
  <section class="detail" aria-label="Détail de l'élément">
    <header class="detail__header">
      <h3>{{ shown.name }}</h3>
      <button type="button" class="secondary" @click="$emit('close')">Fermer</button>
    </header>

    <ApprovalBanner :element="shown" @changed="(next) => (shown = next)" />

    <dl class="fields">
      <div class="field">
        <dt>Type</dt>
        <dd>{{ typeLabel }}</dd>
      </div>
      <div class="field">
        <dt>Couche</dt>
        <dd>{{ LAYER_LABELS[shown.layer] }}</dd>
      </div>
      <div class="field">
        <dt>Aspect</dt>
        <dd>{{ ASPECT_LABELS[shown.aspect] }}</dd>
      </div>
      <div class="field field--wide">
        <dt>Paquet</dt>
        <dd v-if="shown.package_id && packagePath">
          <RouterLink :to="{ path: '/elements', query: { package: shown.package_id } }">
            {{ packagePath }}
          </RouterLink>
        </dd>
        <dd v-else-if="shown.package_id">Rangé dans un paquet</dd>
        <dd v-else class="empty">À la racine</dd>
      </div>
      <div class="field">
        <dt>Créé le</dt>
        <dd>{{ day(shown.created_at) }}</dd>
      </div>
      <div class="field">
        <dt>Modifié le</dt>
        <dd>{{ day(shown.updated_at) }}</dd>
      </div>
      <div class="field field--wide">
        <dt>Identifiant</dt>
        <dd>
          <code>{{ shown.id }}</code>
        </dd>
      </div>
      <div class="field field--wide">
        <dt>Description</dt>
        <dd v-if="shown.description">{{ shown.description }}</dd>
        <dd v-else class="empty">Non renseignée</dd>
      </div>
      <div class="field field--wide">
        <dt>Documentation</dt>
        <dd v-if="shown.documentation" class="documentation">
          {{ shown.documentation }}
        </dd>
        <dd v-else class="empty">Non renseignée</dd>
      </div>
    </dl>

    <section class="attributes" aria-label="Attributs de l'élément">
      <h4>Attributs</h4>
      <p v-if="attributes.length === 0" class="empty">Aucun attribut particulier.</p>
      <dl v-else class="fields">
        <div v-for="[key, value] in attributes" :key="key" class="field">
          <dt>{{ key }}</dt>
          <dd>{{ value }}</dd>
        </div>
      </dl>
    </section>

    <ElementHistory :element-id="shown.id" />

    <p class="actions">
      <RouterLink :to="{ name: 'neighbourhood', query: { element: shown.id } }">
        Voir le voisinage
      </RouterLink>
    </p>
  </section>
</template>

<style scoped>
.detail {
  display: grid;
  gap: 0.9rem;
  padding: 1rem;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.detail__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
}
.detail__header h3 {
  margin: 0;
}
h4 {
  margin: 0 0 0.4rem;
  font-size: 0.95rem;
}
.fields {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
  gap: 0.6rem 1.2rem;
  margin: 0;
}
.field--wide {
  grid-column: 1 / -1;
}
dt {
  font-size: 0.8rem;
  font-weight: 600;
  opacity: 0.75;
}
dd {
  margin: 0.15rem 0 0;
}
.documentation {
  white-space: pre-wrap;
}
.attributes {
  border-top: 1px solid var(--border);
  padding-top: 0.8rem;
}
.empty {
  margin: 0;
  opacity: 0.6;
  font-style: italic;
}
.actions {
  margin: 0;
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
code {
  font-size: 0.9em;
}
</style>
