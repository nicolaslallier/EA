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
import { useMe } from '../../lib/me'
import DocumentPanel from '../documents/DocumentPanel.vue'
import { LAYER_LABELS, useMetamodel } from '../metamodel/useMetamodel'
import PackageManager from '../packages/PackageManager.vue'
import ReportDialog from '../reports/ReportDialog.vue'
import PackageTree from '../packages/PackageTree.vue'
import { ALL, ELEMENT_DRAG_TYPE, ROOT } from '../packages/packageTree'
import { usePackages } from '../packages/usePackages'
import RelationshipPanel from '../relationships/RelationshipPanel.vue'
import ElementDetail from './ElementDetail.vue'
import ElementForm from './ElementForm.vue'
import type {
  ElementCreate,
  ElementRead,
  ElementType,
  ElementUpdate,
  Layer,
} from './useElementCatalogue'
import { useElementCatalogue } from './useElementCatalogue'
import { useAsOf } from '../../lib/asof'
import { useElementDetail } from './useElementDetail'

const route = useRoute()
const router = useRouter()
const { canWrite } = useMe()
const { asOf } = useAsOf()
const catalogue = useElementCatalogue({ asOf: () => asOf.value })
const metamodel = useMetamodel()
const detail = useElementDetail()
const packages = usePackages()

/** `?package=` — `''` all, `'root'` the unfiled, else a package id (known or not). */
const packageParam = computed(() =>
  typeof route.query.package === 'string' ? route.query.package : ALL,
)
const withSubpackages = computed(() => route.query.subpackages !== '0')
/** The package named by the URL, if the tree still holds it. */
const selectedPackage = computed(() => packages.byId.value.get(packageParam.value) ?? null)

function applyUrlFilters(): void {
  catalogue.filters.package = packageParam.value
  catalogue.filters.includeSubpackages = withSubpackages.value
}

// Another day is another catalogue: ask again from the first page.
watch(asOf, () => catalogue.search())

watch([packageParam, withSubpackages], async () => {
  applyUrlFilters()
  await catalogue.search()
})

/** A node of the tree was chosen: a new question, so a history entry. */
async function selectPackage(value: string): Promise<void> {
  const { package: _previous, ...rest } = route.query
  await router.push({ query: value === ALL ? rest : { ...rest, package: value } })
}

async function setSubpackages(include: boolean): Promise<void> {
  const { subpackages: _previous, ...rest } = route.query
  await router.replace({ query: include ? rest : { ...rest, subpackages: '0' } })
}

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

/** A query parameter is `string | string[] | null`; only a string means anything here. */
function oneOf(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

onMounted(async () => {
  applyUrlFilters()
  // A link from the dashboard arrives with `?layer=` / `?type=`: start filtered.
  // Values pass as they stand; the API refuses a layer or type it does not know.
  catalogue.filters.layer = oneOf(route.query.layer) as Layer | ''
  catalogue.filters.elementType = oneOf(route.query.type) as ElementType | ''
  await Promise.all([metamodel.load(), packages.load(), catalogue.load()])
})

/** Filing an element changes nothing about it, only where it sits: reload both counts and rows. */
async function file(elementIds: string[], target: string): Promise<void> {
  failure.value = ''
  try {
    await packages.moveElements(elementIds, target === ROOT ? null : target)
    // The detail on screen shows the package it had: read it again.
    await Promise.all([catalogue.load(), detailedId.value ? detail.open(detailedId.value) : undefined])
  } catch (caught) {
    failure.value = messageOf(caught)
  }
}

/** The row being filed by keyboard, and where to. */
const filing = ref<ElementRead | null>(null)
const filingTarget = ref(ROOT)

async function confirmFiling(): Promise<void> {
  const element = filing.value
  if (element) {
    await file([element.id], filingTarget.value)
    filing.value = null
  }
}

function openFiling(element: ElementRead): void {
  closeEverything()
  filing.value = element
  filingTarget.value = element.package_id ?? ROOT
}

function onDragStart(event: DragEvent, element: ElementRead): void {
  event.dataTransfer?.setData(ELEMENT_DRAG_TYPE, element.id)
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
  }
}

const packagePath = computed(() => {
  const id = detail.element.value?.package_id
  return id ? (packages.byId.value.get(id)?.path ?? '') : ''
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
  filing.value = null
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
      await catalogue.update(editing.value.id, payload)
    } else {
      const created = await catalogue.create(payload as ElementCreate)
      // The API files nothing on creation: an element made while a package is
      // browsed would land at the root and vanish from the filtered list.
      if (selectedPackage.value) {
        await file([created.id], selectedPackage.value.id)
      }
    }
    // The tree counts what each package holds: a write on an element moves them.
    await packages.load()
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
    await packages.load()
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
  <div class="browser">
    <aside class="browser__tree" aria-label="Rangement">
      <PackageManager :store="packages" :selected="packageParam" @select="selectPackage" />
      <PackageTree
        :packages="packages.packages.value"
        :selected="packageParam"
        :can-drop="canWrite"
        @select="selectPackage"
        @drop="(elementId, target) => file([elementId], target)"
      />
      <ReportDialog v-if="selectedPackage" subject-kind="package" :subject-id="selectedPackage.id" />
      <label v-if="selectedPackage" class="subpackages">
        <input
          type="checkbox"
          :checked="withSubpackages"
          @change="setSubpackages(($event.target as HTMLInputElement).checked)"
        />
        Inclure les sous-paquets
      </label>
    </aside>

    <section class="catalogue">
      <header class="catalogue__header">
        <h2>Éléments d'architecture</h2>
        <button v-if="canWrite" type="button" @click="openCreate">Nouvel élément</button>
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
        :package-path="packagePath"
        @close="closeDetail"
        @changed="detail.open(detail.element.value.id)"
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

      <p v-if="failure && !creating && !editing" class="banner banner--error" role="alert">
        {{ failure }}
      </p>

      <form
        v-if="filing"
        class="banner banner--confirm"
        :aria-label="`Déplacer ${filing.name}`"
        @submit.prevent="confirmFiling"
      >
        <label for="filing-target">Déplacer vers</label>
        <select id="filing-target" v-model="filingTarget">
          <option :value="ROOT">Racine (sans paquet)</option>
          <option v-for="entry in packages.packages.value" :key="entry.id" :value="entry.id">
            {{ entry.path }}
          </option>
        </select>
        <button type="submit">Ranger</button>
        <button type="button" class="secondary" @click="filing = null">Renoncer</button>
      </form>

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
            <th scope="col">Modifié</th>
            <th scope="col">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="element in catalogue.items.value"
            :key="element.id"
            :draggable="canWrite"
            @dragstart="onDragStart($event, element)"
          >
            <td>
              <button type="button" class="link" @click="openDetail(element)">
                {{ element.name }}
              </button>
            </td>
            <td>{{ metamodel.labelOf(element.element_type) }}</td>
            <td>{{ LAYER_LABELS[element.layer] }}</td>
            <td>{{ day(element.updated_at) }}</td>
            <td class="row-actions">
              <button
                v-if="canWrite"
                type="button"
                :aria-label="`Modifier ${element.name}`"
                @click="openEdit(element)"
              >
                Modifier
              </button>
              <button
                v-if="canWrite"
                type="button"
                :aria-label="`Déplacer ${element.name}`"
                @click="openFiling(element)"
              >
                Déplacer vers…
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
                v-if="canWrite"
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
  </div>
</template>

<style scoped>
.browser {
  display: grid;
  grid-template-columns: minmax(12rem, 16rem) minmax(0, 1fr);
  gap: 1.2rem;
  align-items: start;
}
.browser__tree {
  display: grid;
  gap: 0.8rem;
}
.subpackages {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.9rem;
}
@media (max-width: 48rem) {
  .browser {
    grid-template-columns: minmax(0, 1fr);
  }
}
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
