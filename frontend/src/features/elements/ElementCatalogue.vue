<script setup lang="ts">
// The catalogue screen: browse, create, edit and delete architecture elements,
// and open the relations of one of them.
//
// It owns the interaction — which form or panel is open, which row is awaiting
// a confirmation — and delegates every rule to the API. The three composables
// it uses hold the data: one for the page of elements, one for the palette,
// one for the element being looked at; the relations panel owns its own.
//
// Which element is detailed is the one piece of this screen's state a user
// would send to a colleague, so it lives in the URL (`?element=`) rather than
// in a ref — like the neighbourhood, see docs/adr/0010. An unsaved form is not
// that kind of state, and stays local.
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { messageOf } from '../../lib/api'
import DocumentPanel from '../documents/DocumentPanel.vue'
import { LAYER_LABELS, useMetamodel } from '../metamodel/useMetamodel'
import RelationshipPanel from '../relationships/RelationshipPanel.vue'
import ElementDetail from './ElementDetail.vue'
import ElementForm from './ElementForm.vue'
import type { ElementCreate, ElementRead, ElementUpdate } from './useElementCatalogue'
import { useElementCatalogue } from './useElementCatalogue'
import { useElementDetail } from './useElementDetail'

const route = useRoute()
const router = useRouter()
const catalogue = useElementCatalogue()
const metamodel = useMetamodel()
const detail = useElementDetail()

/** The element the URL asks to detail, if it asks for one. */
const detailedId = computed(() =>
  typeof route.query.element === 'string' ? route.query.element : '',
)

watch(
  detailedId,
  async (id) => {
    if (!id) {
      detail.close()
      return
    }
    await detail.open(id)
  },
  { immediate: true },
)

/** Which form is open: none, a blank one, or one editing a stored element. */
const editing = ref<ElementRead | null>(null)
const creating = ref(false)
/** The row whose deletion is waiting for a confirmation. */
const confirming = ref<ElementRead | null>(null)
/** The element whose relations are on screen, or nothing. */
const relating = ref<ElementRead | null>(null)
/** The element whose attached markdown is on screen, or nothing. */
const documenting = ref<ElementRead | null>(null)
const failure = ref('')
const busy = ref(false)

onMounted(async () => {
  await Promise.all([metamodel.load(), catalogue.load()])
})

/**
 * Show the detail of one element by asking for it in the URL.
 *
 * Opening pushes, so the back button closes the detail; closing replaces, so a
 * dozen opened-and-closed elements do not bury the page the user came from.
 */
async function openDetail(element: ElementRead): Promise<void> {
  await router.push({ query: { ...route.query, element: element.id } })
}

async function closeDetail(): Promise<void> {
  if (!detailedId.value) {
    return
  }
  const { element: _detailed, ...rest } = route.query
  await router.replace({ query: rest })
}

// One panel at a time: acting on an element replaces the reading of it, so the
// screen never shows the same element twice, once read-only and once in a form.
function closeEverything(): void {
  failure.value = ''
  creating.value = false
  editing.value = null
  relating.value = null
  documenting.value = null
}

async function openCreate(): Promise<void> {
  closeEverything()
  creating.value = true
  await closeDetail()
}

/** Show the relations of one row. Only one panel at a time, like the form. */
async function openRelations(element: ElementRead): Promise<void> {
  closeEverything()
  relating.value = element
  await closeDetail()
}

/** Show the markdown attached to one row — same rule, its own panel. */
async function openDocuments(element: ElementRead): Promise<void> {
  closeEverything()
  documenting.value = element
  await closeDetail()
}

async function openEdit(element: ElementRead): Promise<void> {
  closeEverything()
  editing.value = element
  await closeDetail()
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

    <p v-if="detail.status.value === 'error'" class="banner banner--error" role="alert">
      {{ detail.error.value }}
    </p>

    <p v-if="detail.status.value === 'loading'" class="hint">Chargement de l'élément…</p>

    <ElementDetail
      v-if="detail.element.value"
      :key="detail.element.value.id"
      :element="detail.element.value"
      :type-label="metamodel.labelOf(detail.element.value.element_type)"
      @close="closeDetail"
    />

    <RelationshipPanel
      v-if="relating"
      :key="relating.id"
      :element="relating"
      @close="relating = null"
    />

    <DocumentPanel
      v-if="documenting"
      :key="documenting.id"
      :element="documenting"
      @close="documenting = null"
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
          <td>
            <button type="button" class="link" @click="openDetail(element)">
              {{ element.name }}
            </button>
          </td>
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
              :aria-label="`Documents de ${element.name}`"
              @click="openDocuments(element)"
            >
              Documents
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
/* The name is a real control — focusable and announced as a button — drawn as
   the link it behaves like. */
.link {
  padding: 0;
  border: none;
  background: none;
  color: inherit;
  font: inherit;
  font-weight: 600;
  text-align: left;
  text-decoration: underline;
  text-underline-offset: 2px;
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
