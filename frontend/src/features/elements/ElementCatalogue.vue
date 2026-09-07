<script setup lang="ts">
// The catalogue screen: browse, create, edit and delete architecture elements,
// and open the relations of one of them.
//
// It owns the interaction — which form or panel is open, which row is awaiting
// a confirmation — and delegates every rule to the API. The two composables it
// uses hold the data: one for the page of elements, one for the palette; the
// relations panel owns its own.
import { onMounted, ref } from 'vue'

import { messageOf } from '../../lib/api'
import { LAYER_LABELS, useMetamodel } from '../metamodel/useMetamodel'
import RelationshipPanel from '../relationships/RelationshipPanel.vue'
import ElementForm from './ElementForm.vue'
import type { ElementCreate, ElementRead, ElementUpdate } from './useElementCatalogue'
import { useElementCatalogue } from './useElementCatalogue'

const catalogue = useElementCatalogue()
const metamodel = useMetamodel()

/** Which form is open: none, a blank one, or one editing a stored element. */
const editing = ref<ElementRead | null>(null)
const creating = ref(false)
/** The row whose deletion is waiting for a confirmation. */
const confirming = ref<ElementRead | null>(null)
/** The element whose relations are on screen, or nothing. */
const relating = ref<ElementRead | null>(null)
const failure = ref('')
const busy = ref(false)

onMounted(async () => {
  await Promise.all([metamodel.load(), catalogue.load()])
})

function openCreate(): void {
  failure.value = ''
  editing.value = null
  creating.value = true
}

/** Show the relations of one row. Only one panel at a time, like the form. */
function openRelations(element: ElementRead): void {
  failure.value = ''
  creating.value = false
  editing.value = null
  relating.value = element
}

function openEdit(element: ElementRead): void {
  failure.value = ''
  creating.value = false
  editing.value = element
}

function closeForm(): void {
  creating.value = false
  editing.value = null
  failure.value = ''
}

async function onSubmit(payload: ElementCreate | ElementUpdate): Promise<void> {
  busy.value = true
  failure.value = ''
  try {
    if (editing.value) {
      await catalogue.update(editing.value.id, payload as ElementUpdate)
    } else {
      await catalogue.create(payload as ElementCreate)
    }
    // Closing only here keeps what the user typed on screen when the API
    // refuses the element — a duplicate name, say.
    closeForm()
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

async function confirmDelete(): Promise<void> {
  const element = confirming.value
  if (!element) {
    return
  }
  busy.value = true
  try {
    await catalogue.remove(element.id)
    confirming.value = null
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

async function goToPage(target: number): Promise<void> {
  catalogue.goTo(target)
  await catalogue.load()
}

/** Dates come as ISO strings; the table only needs the day. */
function day(iso: string): string {
  return iso.slice(0, 10)
}
</script>

<template>
  <section class="catalogue">
    <header class="catalogue__header">
      <h2>Éléments d'architecture</h2>
      <button type="button" @click="openCreate">Nouvel élément</button>
    </header>

    <form class="filters" role="search" aria-label="Filtrer le catalogue"
          @submit.prevent="catalogue.search()">
      <div class="field">
        <label for="filter-search">Rechercher</label>
        <input id="filter-search" v-model="catalogue.filters.search" type="search"
               placeholder="Un fragment de nom" />
      </div>
      <div class="field">
        <label for="filter-type">Type</label>
        <select id="filter-type" v-model="catalogue.filters.elementType"
                @change="catalogue.search()">
          <option value="">Tous les types</option>
          <optgroup v-for="group in metamodel.byLayer.value" :key="group.layer"
                    :label="LAYER_LABELS[group.layer]">
            <option v-for="type in group.types" :key="type.value" :value="type.value">
              {{ type.label }}
            </option>
          </optgroup>
        </select>
      </div>
      <div class="field">
        <label for="filter-layer">Couche</label>
        <select id="filter-layer" v-model="catalogue.filters.layer" @change="catalogue.search()">
          <option value="">Toutes les couches</option>
          <option v-for="layer in metamodel.layers.value" :key="layer" :value="layer">
            {{ LAYER_LABELS[layer] }}
          </option>
        </select>
      </div>
      <button type="submit">Filtrer</button>
    </form>

    <p v-if="catalogue.status.value === 'error'" class="banner banner--error" role="alert">
      {{ catalogue.error.value }}
    </p>

    <ElementForm
      v-if="creating || editing"
      :key="editing?.id ?? 'new'"
      :layer-groups="metamodel.byLayer.value"
      :element="editing"
      :failure="failure"
      :busy="busy"
      @submit="onSubmit"
      @cancel="closeForm"
    />

    <RelationshipPanel
      v-if="relating"
      :key="relating.id"
      :element="relating"
      @close="relating = null"
    />

    <p v-if="confirming" class="banner banner--confirm" role="alert">
      Supprimer « {{ confirming.name }} » et toutes ses relations ?
      <button type="button" :disabled="busy" @click="confirmDelete">Confirmer</button>
      <button type="button" class="secondary" @click="confirming = null">Renoncer</button>
    </p>

    <p v-if="catalogue.status.value === 'loading'" class="hint">Chargement du catalogue…</p>

    <p v-else-if="catalogue.items.value.length === 0 && catalogue.status.value === 'ready'"
       class="hint">
      Aucun élément pour l'instant. Commence par « Nouvel élément ».
    </p>

    <table v-else-if="catalogue.items.value.length > 0">
      <caption class="sr-only">Catalogue des éléments d'architecture</caption>
      <thead>
        <tr>
          <th scope="col">Nom</th>
          <th scope="col">Type</th>
          <th scope="col">Couche</th>
          <th scope="col">Description</th>
          <th scope="col">Modifié</th>
          <th scope="col">Actions</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="element in catalogue.items.value" :key="element.id">
          <td>{{ element.name }}</td>
          <td>{{ metamodel.labelOf(element.element_type) }}</td>
          <td>{{ LAYER_LABELS[element.layer] }}</td>
          <td class="description">{{ element.description }}</td>
          <td>{{ day(element.updated_at) }}</td>
          <td class="row-actions">
            <button type="button" :aria-label="`Modifier ${element.name}`" @click="openEdit(element)">
              Modifier
            </button>
            <button
              type="button"
              :aria-label="`Relations de ${element.name}`"
              @click="openRelations(element)"
            >
              Relations
            </button>
            <button
              type="button"
              class="secondary"
              :aria-label="`Supprimer ${element.name}`"
              @click="confirming = element"
            >
              Supprimer
            </button>
          </td>
        </tr>
      </tbody>
    </table>

    <nav v-if="catalogue.total.value > 0" class="pagination" aria-label="Pagination du catalogue">
      <button
        type="button"
        :disabled="!catalogue.hasPreviousPage.value"
        @click="goToPage(catalogue.page.value - 1)"
      >
        Page précédente
      </button>
      <span>
        Page {{ catalogue.page.value + 1 }} sur {{ catalogue.pageCount.value }}
        — {{ catalogue.total.value }} éléments
      </span>
      <button
        type="button"
        :disabled="!catalogue.hasNextPage.value"
        @click="goToPage(catalogue.page.value + 1)"
      >
        Page suivante
      </button>
    </nav>
  </section>
</template>

<style scoped>
.catalogue {
  display: grid;
  gap: 1rem;
}
.catalogue__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
}
.filters {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  gap: 0.6rem;
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
table {
  width: 100%;
  border-collapse: collapse;
}
th,
td {
  text-align: left;
  padding: 0.5rem 0.6rem;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}
.description {
  max-width: 28rem;
}
.row-actions {
  display: flex;
  gap: 0.4rem;
}
.pagination {
  display: flex;
  align-items: center;
  gap: 0.8rem;
}
.hint {
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
