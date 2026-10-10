<script setup lang="ts">
// The `/recherche` section: build a structured search, read its results, keep it.
//
// The address is the question — `?q=` carries a filter that fits in a link
// (2 000 characters at most), `?saved=` names a shared query — so a search can
// be sent to a colleague, and the back button leaves it. Submitting a filter
// writes `?q=` (a history entry: it is a new question); paging does not touch
// the address. A filter too long for a link is still run, and the person is
// told the way to share it is to save it.
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { LAYER_LABELS, useMetamodel } from '../metamodel/useMetamodel'
import { usePackages } from '../packages/usePackages'
import QueryBuilder from './QueryBuilder.vue'
import {
  PAGE_SIZE,
  cloneQuery,
  decodeQuery,
  emptyQuery,
  encodeQuery,
  type Query,
} from './queryState'
import SavedQueries from './SavedQueries.vue'
import { useElementSearch } from './useElementSearch'
import { useSavedQueries } from './useSavedQueries'

const route = useRoute()
const router = useRouter()
const metamodel = useMetamodel()
const packages = usePackages()
const results = useElementSearch()
const saved = useSavedQueries()

function one(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

const savedId = computed(() => one(route.query.saved))
const rawQuery = computed(() => one(route.query.q))

/** The filter on screen — what the form edits, and what "Rechercher" sends. */
const query = ref<Query>(emptyQuery())
const page = ref(0)
/** What the address could not give: said in words, next to the form. */
const addressProblem = ref('')
const tooLongForALink = ref(false)
const hasAsked = ref(false)

const pageCount = computed(() => Math.max(1, Math.ceil(results.total.value / PAGE_SIZE)))

onMounted(async () => {
  await Promise.all([metamodel.load(), packages.load(), saved.load()])
})

/** Run what the screen holds, at the current page. */
async function run(filter: Query = query.value): Promise<void> {
  hasAsked.value = true
  const offset = page.value * PAGE_SIZE
  if (savedId.value && saved.opened.value) {
    await results.runSaved(savedId.value, PAGE_SIZE, offset)
  } else {
    lastRun = cloneQuery(filter)
    await results.run({ ...cloneQuery(filter), limit: PAGE_SIZE, offset })
  }
}

// The filter the results on screen answer: paging goes on with it, not with
// whatever has been edited in the form since.
let lastRun: Query | null = null

/** The address changed: read the question it asks and answer it. */
async function readAddress(): Promise<void> {
  addressProblem.value = ''
  page.value = 0
  if (savedId.value) {
    await saved.open(savedId.value)
    if (!saved.opened.value) {
      results.clear()
      return
    }
    query.value = cloneQuery(saved.opened.value.query)
    await run()
  } else if (rawQuery.value) {
    saved.close()
    const decoded = decodeQuery(rawQuery.value)
    if (!decoded) {
      addressProblem.value = "Le filtre de l'adresse est illisible : construis-le dans le formulaire."
      query.value = emptyQuery()
      results.clear()
      return
    }
    query.value = decoded
    await run()
  } else {
    saved.close()
    query.value = emptyQuery()
    hasAsked.value = false
    results.clear()
  }
}

// Set when the screen itself empties the address (a filter too long to keep in
// one): the watcher must not then read that empty address and wipe the form.
let addressWasEmptiedByUs = false

watch(
  [savedId, rawQuery],
  async () => {
    if (addressWasEmptiedByUs) {
      addressWasEmptiedByUs = false
      return
    }
    await readAddress()
  },
  { immediate: true },
)

/** "Rechercher": a new question, so a history entry — and an address, if it fits in one. */
async function submit(): Promise<void> {
  const encoded = encodeQuery(query.value)
  tooLongForALink.value = encoded === null
  page.value = 0
  if (encoded !== null) {
    if (encoded === rawQuery.value && !savedId.value) {
      // The address would not change, so the watcher would not fire: ask again here.
      await run()
      return
    }
    await router.push({ query: { q: encoded } })
    return
  }
  // Too long to share by link: run it anyway, from a clean address.
  saved.close()
  if (savedId.value || rawQuery.value) {
    addressWasEmptiedByUs = true
    await router.replace({ query: {} })
  }
  await run()
}

async function openSaved(id: string): Promise<void> {
  tooLongForALink.value = false
  if (id === savedId.value) {
    await readAddress()
    return
  }
  await router.push({ query: { saved: id } })
}

/** A deleted query must not stay in the address: it would reopen into a 404. */
async function forget(id: string): Promise<void> {
  if (id === savedId.value) {
    await router.replace({ query: {} })
  }
}

async function goToPage(target: number): Promise<void> {
  page.value = Math.max(0, target)
  await run(lastRun ?? query.value)
}

/** One message for the two places a question can fail: opening the saved query, running it. */
const sourceError = computed(() => saved.openError.value || results.error.value)
/** The palette and the package list are conveniences, but an empty form must say why it is empty. */
const supportError = computed(() => metamodel.error.value || packages.error.value)
</script>

<template>
  <section class="search">
    <header>
      <h2>Recherche</h2>
      <p class="hint">
        Types, couches, nom, paquet, attributs et relations présentes ou absentes. Les critères se
        combinent par « et ».
      </p>
    </header>

    <div class="layout">
      <div class="main">
        <QueryBuilder v-model="query" :metamodel="metamodel" :packages="packages.packages.value" @submit="submit" />

        <p v-if="tooLongForALink" class="banner" role="status">
          Ce filtre est trop long pour une adresse : enregistre-le pour le partager.
        </p>
        <p v-if="addressProblem" class="banner banner--error" role="alert">{{ addressProblem }}</p>
        <p v-if="sourceError" class="banner banner--error" role="alert">{{ sourceError }}</p>
        <p v-if="supportError" class="banner banner--error" role="alert">{{ supportError }}</p>

        <p v-if="!hasAsked && !addressProblem && !sourceError" class="hint">
          Définis un filtre, puis « Rechercher ».
        </p>
        <p v-else-if="results.status.value === 'loading'" class="hint">Recherche…</p>
        <p v-else-if="results.status.value === 'ready' && results.items.value.length === 0" class="hint">
          Aucun élément ne correspond.
        </p>

        <template v-if="results.items.value.length > 0">
          <p class="hint" aria-live="polite">
            {{ results.total.value }} résultat{{ results.total.value > 1 ? 's' : '' }}
          </p>
          <table>
            <caption class="sr-only">Résultats de la recherche</caption>
            <thead>
              <tr>
                <th scope="col">Nom</th>
                <th scope="col">Type</th>
                <th scope="col">Couche</th>
                <th scope="col">Paquet</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="element in results.items.value" :key="element.id">
                <td>
                  <RouterLink :to="{ path: '/elements', query: { element: element.id } }">
                    {{ element.name }}
                  </RouterLink>
                </td>
                <td>{{ metamodel.labelOf(element.element_type) }}</td>
                <td>{{ LAYER_LABELS[element.layer] }}</td>
                <td>
                  {{
                    element.package_id
                      ? (packages.byId.value.get(element.package_id)?.path ?? '—')
                      : 'Racine'
                  }}
                </td>
              </tr>
            </tbody>
          </table>

          <nav class="pagination" aria-label="Pagination des résultats">
            <button type="button" :disabled="page === 0" @click="goToPage(page - 1)">
              Page précédente
            </button>
            <span>Page {{ page + 1 }} sur {{ pageCount }}</span>
            <button type="button" :disabled="page + 1 >= pageCount" @click="goToPage(page + 1)">
              Page suivante
            </button>
          </nav>
        </template>
      </div>

      <aside class="side">
        <SavedQueries :store="saved" :current="query" :active-id="savedId" @open="openSaved" @removed="forget" />
      </aside>
    </div>
  </section>
</template>

<style scoped>
.search {
  display: grid;
  gap: 1rem;
}
header h2 {
  margin: 0 0 0.2rem;
}
.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(14rem, 20rem);
  gap: 1.2rem;
  align-items: start;
}
.main {
  display: grid;
  gap: 0.8rem;
  min-width: 0;
}
@media (max-width: 56rem) {
  .layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
table {
  width: 100%;
  border-collapse: collapse;
}
th,
td {
  text-align: left;
  padding: 0.45rem 0.6rem;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}
.pagination {
  display: flex;
  align-items: center;
  gap: 0.8rem;
}
.pagination button {
  font: inherit;
  padding: 0.4rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
}
.pagination button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.hint {
  opacity: 0.75;
  margin: 0;
}
.banner {
  margin: 0;
  padding: 0.5rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.banner--error {
  border-color: #c62828;
  color: #c62828;
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
