<script setup lang="ts">
// The `/analyse/voisinage` section: pick an element, then look around it.
//
// The question lives in the URL — which element, how deep, which relationships
// — rather than in a local ref. Three things follow from that: the view is a
// link one can send to a colleague, the browser's back button walks back
// through an exploration, and this component has one code path, whether the
// element was chosen in the picker, clicked on the drawing or typed in the
// address bar.
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useElementCatalogue } from '../elements/useElementCatalogue'
import { useMetamodel } from '../metamodel/useMetamodel'
import { RELATIONSHIP_LABELS } from '../relationships/labels'
import NeighbourhoodGraph from './NeighbourhoodGraph.vue'
import type { ElementRead, RelationshipType } from './useNeighbourhood'
import { DEFAULT_DEPTH, MAX_DEPTH, useNeighbourhood } from './useNeighbourhood'

const route = useRoute()
const router = useRouter()
const catalogue = useElementCatalogue()
const metamodel = useMetamodel()
const neighbourhood = useNeighbourhood()

const DEPTHS = Array.from({ length: MAX_DEPTH }, (_, index) => index + 1)

/** A query parameter is `string | string[] | null`; only one value means anything here. */
function one(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

const focusId = computed(() => one(route.query.element))

const depth = computed(() => {
  const asked = Number.parseInt(one(route.query.depth), 10)
  return Number.isNaN(asked) ? DEFAULT_DEPTH : Math.min(MAX_DEPTH, Math.max(1, asked))
})

// A relationship type typed by hand is passed on as it stands: the API knows
// the eleven types and refuses anything else, and its refusal is the message
// the user should read.
const relationshipType = computed(() => one(route.query.relation) as RelationshipType | '')

/** The picker cannot name an element that is not on the page it is showing. */
const offPage = computed(() => {
  const subject = neighbourhood.subject.value
  return subject && !catalogue.items.value.some((element) => element.id === subject.id)
    ? subject
    : null
})

const alone = computed(
  () => neighbourhood.status.value === 'ready' && neighbourhood.neighbours.value === 0,
)

onMounted(async () => {
  await Promise.all([metamodel.load(), catalogue.load()])
})

async function load(): Promise<void> {
  if (!focusId.value) {
    neighbourhood.clear()
    return
  }
  await neighbourhood.explore(focusId.value, {
    depth: depth.value,
    relationshipType: relationshipType.value,
  })
}

watch([focusId, depth, relationshipType], load, { immediate: true })

/**
 * Ask a new question by changing the URL.
 *
 * Changing subject is a step of an exploration, so it is pushed and the back
 * button undoes it; turning a dial on the same subject only replaces, or a
 * dozen depth changes would bury the previous element in the history.
 */
function ask(changes: Record<string, string | undefined>, step = false): void {
  const query = { ...route.query, ...changes }
  void (step ? router.push({ query }) : router.replace({ query }))
}

function onPick(event: Event): void {
  const chosen = (event.target as HTMLSelectElement).value
  ask({ element: chosen || undefined }, true)
}

function recentre(element: ElementRead): void {
  ask({ element: element.id }, true)
}

// The chosen element may not survive a narrower filter; the drawing stays on
// screen either way, because the URL — not the picker — says what is drawn.
const onSearch = () => catalogue.search()
</script>

<template>
  <section class="neighbourhood">
    <header>
      <h2>Voisinage</h2>
      <p class="hint">Le sous-graphe autour d'un élément, à profondeur choisie.</p>
    </header>

    <p v-if="catalogue.status.value === 'error'" class="banner banner--error" role="alert">
      {{ catalogue.error.value }}
    </p>

    <form class="controls" role="search" aria-label="Choisir un élément"
          @submit.prevent="onSearch">
      <div class="field">
        <label for="neighbourhood-search">Rechercher</label>
        <input id="neighbourhood-search" v-model="catalogue.filters.search" type="search"
               placeholder="Un fragment de nom" />
      </div>

      <div class="field">
        <label for="neighbourhood-element">Élément</label>
        <select id="neighbourhood-element" :value="focusId" @change="onPick">
          <option value="">Choisis un élément…</option>
          <option v-if="offPage" :value="offPage.id">{{ offPage.name }}</option>
          <option v-for="element in catalogue.items.value" :key="element.id" :value="element.id">
            {{ element.name }}
          </option>
        </select>
      </div>

      <div class="field">
        <label for="neighbourhood-depth">Profondeur</label>
        <select id="neighbourhood-depth" :value="String(depth)"
                @change="ask({ depth: ($event.target as HTMLSelectElement).value })">
          <option v-for="hop in DEPTHS" :key="hop" :value="String(hop)">
            {{ hop }} saut{{ hop > 1 ? 's' : '' }}
          </option>
        </select>
      </div>

      <div class="field">
        <label for="neighbourhood-relation">Relation suivie</label>
        <select id="neighbourhood-relation" :value="relationshipType"
                @change="ask({ relation: ($event.target as HTMLSelectElement).value || undefined })">
          <option value="">Toutes les relations</option>
          <option v-for="type in metamodel.byStrength.value" :key="type.value" :value="type.value">
            {{ RELATIONSHIP_LABELS[type.value] }}
          </option>
        </select>
      </div>

      <button type="submit">Filtrer</button>
    </form>

    <p v-if="!focusId" class="hint">
      Choisis un élément pour voir ce qui l'entoure. Un clic sur un voisin déplace le centre.
    </p>

    <template v-else>
      <p v-if="neighbourhood.status.value === 'error'" class="banner banner--error" role="alert">
        {{ neighbourhood.error.value }}
      </p>

      <p v-else-if="neighbourhood.status.value === 'loading'" class="hint">
        Chargement du voisinage…
      </p>

      <template v-else-if="neighbourhood.subject.value">
        <h3 class="subject">Voisinage de « {{ neighbourhood.subject.value.name }} »</h3>

        <p v-if="alone" class="hint">
          « {{ neighbourhood.subject.value.name }} » n'a aucun voisin à
          {{ depth }} saut{{ depth > 1 ? 's' : '' }}<template v-if="relationshipType">
            par la relation « {{ RELATIONSHIP_LABELS[relationshipType] }} »</template>.
        </p>

        <NeighbourhoodGraph
          :graph="neighbourhood.graph.value"
          :root-id="focusId"
          :type-label="metamodel.labelOf"
          @focus="recentre"
        />
      </template>
    </template>
  </section>
</template>

<style scoped>
.neighbourhood {
  display: grid;
  gap: 1rem;
}
header h2 {
  margin: 0 0 0.2rem;
}
.subject {
  margin: 0;
}
.controls {
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
.hint {
  margin: 0;
  opacity: 0.75;
}
.banner {
  margin: 0;
  padding: 0.6rem 0.9rem;
  border: 1px solid #c62828;
  border-radius: 6px;
  color: #c62828;
}
</style>
