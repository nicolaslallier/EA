<script setup lang="ts">
// The `/analyse/impact` section: pick an element, then see what breaks with it.
//
// The same shape as the neighbourhood, and a different question. The
// neighbourhood asks "what is around this", ignoring arrows; the impact
// analysis asks "what depends on this", and every hop is walked the way
// dependency actually runs — forwards along a serving, backwards along a
// composition. So the ring of a box is no longer a distance: it is how far a
// failure travels before reaching it.
//
// The answer is given twice on purpose. The drawing shows the *shape* of the
// cascade; the list underneath is the verdict one reads out in a change
// meeting — ordered by distance, then by name.
//
// The question lives in the URL, not in a ref: the view is a link one can send
// to a colleague, the back button walks an investigation back, and this
// component has one code path whether the element came from the picker, from a
// click on the drawing or from the address bar (`docs/adr/0010`).
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import GraphDiagram from '../../components/GraphDiagram.vue'
import { useElementCatalogue } from '../elements/useElementCatalogue'
import { LAYER_COLOURS, LAYER_LABELS, useMetamodel } from '../metamodel/useMetamodel'
import { RELATIONSHIP_LABELS } from '../relationships/labels'
import type { ElementRead, RelationshipType } from './useImpact'
import { DEFAULT_DEPTH, MAX_DEPTH, useImpact } from './useImpact'

const route = useRoute()
const router = useRouter()
const catalogue = useElementCatalogue()
const metamodel = useMetamodel()
// The direction rule is handed in rather than fetched here: it is a fact of
// the metamodel, and `useMetamodel` is the one place that reads it.
const impact = useImpact(metamodel.followsArrow)

const DEPTHS = Array.from({ length: MAX_DEPTH }, (_, index) => index + 1)

/** A query parameter is `string | string[] | null`; only one value means anything here. */
function one(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

const subjectId = computed(() => one(route.query.element))

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
  const subject = impact.subject.value
  return subject && !catalogue.items.value.some((element) => element.id === subject.id)
    ? subject
    : null
})

/** How far the cascade actually travelled — never further than it was allowed. */
const reach = computed(() => impact.waves.value.length)

function hop(count: number): string {
  return `${count} saut${count > 1 ? 's' : ''}`
}

function focusAction(element: ElementRead): string {
  return `Analyser l'impact de ${element.name}`
}

/** The answer in one sentence — the line that gets read out in a meeting. */
const verdict = computed(() => {
  const name = impact.subject.value?.name ?? ''
  const count = impact.impacted.value
  if (count === 0) {
    const only = relationshipType.value
      ? ` par la relation « ${RELATIONSHIP_LABELS[relationshipType.value]} »`
      : ''
    return `Rien ne dépend de « ${name} » à ${hop(depth.value)}${only}.`
  }
  return (
    `${count} élément${count > 1 ? 's' : ''} ` +
    `dépend${count > 1 ? 'ent' : ''} de « ${name} », jusqu'à ${hop(reach.value)}.`
  )
})

onMounted(async () => {
  await Promise.all([metamodel.load(), catalogue.load()])
})

async function load(): Promise<void> {
  if (!subjectId.value) {
    impact.clear()
    return
  }
  await impact.analyse(subjectId.value, {
    depth: depth.value,
    relationshipType: relationshipType.value,
  })
}

watch([subjectId, depth, relationshipType], load, { immediate: true })

/**
 * Ask a new question by changing the URL.
 *
 * Changing subject is a step of an investigation, so it is pushed and the back
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

/** Following the cascade one step further: what breaks when *that* breaks. */
function analyse(element: ElementRead): void {
  ask({ element: element.id }, true)
}

const onSearch = () => catalogue.search()
</script>

<template>
  <section class="impact">
    <header>
      <h2>Analyse d'impact</h2>
      <p class="hint">Ce qui dépend d'un élément, de proche en proche.</p>
    </header>

    <p v-if="catalogue.status.value === 'error'" class="banner banner--error" role="alert">
      {{ catalogue.error.value }}
    </p>

    <form class="controls" role="search" aria-label="Choisir un élément"
          @submit.prevent="onSearch">
      <div class="field">
        <label for="impact-search">Rechercher</label>
        <input id="impact-search" v-model="catalogue.filters.search" type="search"
               placeholder="Un fragment de nom" />
      </div>

      <div class="field">
        <label for="impact-element">Élément</label>
        <select id="impact-element" :value="subjectId" @change="onPick">
          <option value="">Choisis un élément…</option>
          <option v-if="offPage" :value="offPage.id">{{ offPage.name }}</option>
          <option v-for="element in catalogue.items.value" :key="element.id" :value="element.id">
            {{ element.name }}
          </option>
        </select>
      </div>

      <div class="field">
        <label for="impact-depth">Profondeur</label>
        <select id="impact-depth" :value="String(depth)"
                @change="ask({ depth: ($event.target as HTMLSelectElement).value })">
          <option v-for="count in DEPTHS" :key="count" :value="String(count)">
            {{ hop(count) }}
          </option>
        </select>
      </div>

      <div class="field">
        <label for="impact-relation">Relation suivie</label>
        <select id="impact-relation" :value="relationshipType"
                @change="ask({ relation: ($event.target as HTMLSelectElement).value || undefined })">
          <option value="">Toutes les relations</option>
          <option v-for="type in metamodel.byStrength.value" :key="type.value" :value="type.value">
            {{ RELATIONSHIP_LABELS[type.value] }}
          </option>
        </select>
      </div>

      <button type="submit">Filtrer</button>
    </form>

    <p v-if="!subjectId" class="hint">
      Choisis un élément pour voir ce qui tombe avec lui. Un clic sur un élément impacté
      poursuit l'enquête à partir de celui-ci.
    </p>

    <template v-else>
      <p v-if="impact.status.value === 'error'" class="banner banner--error" role="alert">
        {{ impact.error.value }}
      </p>

      <p v-else-if="impact.status.value === 'loading'" class="hint">Analyse en cours…</p>

      <template v-else-if="impact.subject.value">
        <h3 class="subject">Impact de « {{ impact.subject.value.name }} »</h3>

        <p class="verdict" :class="{ 'verdict--clear': impact.impacted.value === 0 }">
          {{ verdict }}
        </p>

        <GraphDiagram
          :graph="impact.graph.value"
          :root-id="subjectId"
          :hops="impact.hops.value"
          :inert="impact.inert.value"
          :type-label="metamodel.labelOf"
          caption="Impact"
          :focus-action="focusAction"
          @focus="analyse"
        />

        <ol v-if="impact.waves.value.length > 0" class="cascade" aria-label="Éléments impactés">
          <li v-for="wave in impact.waves.value" :key="wave.hops" class="cascade__wave">
            <h4>À {{ hop(wave.hops) }}</h4>
            <ul class="cascade__elements">
              <li v-for="element in wave.elements" :key="element.id">
                <span class="cascade__swatch" :style="{ background: LAYER_COLOURS[element.layer] }"
                      aria-hidden="true" />
                <span class="cascade__name">{{ element.name }}</span>
                <span class="cascade__type">
                  {{ metamodel.labelOf(element.element_type) }} — {{ LAYER_LABELS[element.layer] }}
                </span>
              </li>
            </ul>
          </li>
        </ol>
      </template>
    </template>
  </section>
</template>

<style scoped>
.impact {
  display: grid;
  gap: 1rem;
}
header h2 {
  margin: 0 0 0.2rem;
}
.subject {
  margin: 0;
}
.verdict {
  margin: 0;
}
.verdict--clear {
  opacity: 0.75;
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
.cascade {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 0.9rem;
}
.cascade__wave h4 {
  margin: 0 0 0.35rem;
  font-size: 0.8rem;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  opacity: 0.6;
}
.cascade__elements {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 0.3rem;
}
.cascade__elements li {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.5rem;
  padding: 0.4rem 0.6rem;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.cascade__swatch {
  align-self: center;
  width: 0.8rem;
  height: 0.8rem;
  border: 1px solid #6f6b78;
  border-radius: 3px;
}
.cascade__name {
  font-weight: 600;
}
.cascade__type {
  font-size: 0.85rem;
  opacity: 0.7;
}
</style>
