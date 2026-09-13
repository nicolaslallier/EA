<script setup lang="ts">
// The `/metamodele` section: what ArchiMate 3.2 lets one model, and connect.
//
// Everything drawn here comes from `/metamodel` and `/metamodel/matrix` — the
// same code the API validates a link with. The SPA holds no copy of the 61
// types, of the 11 relationships, nor of the 61x61 matrix: a rule that changed
// on one side and not the other would let this screen promise a link the API
// then refuses.
//
// The question — which source type, which relationship — lives in the URL, so
// a cell of the matrix is something one can send to a colleague and the back
// button walks an exploration back. See docs/adr/0012.
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { RELATIONSHIP_LABELS } from '../relationships/labels'
import type { ElementType, RelationshipType } from './useMetamodel'
import {
  ASPECT_LABELS,
  BOX_TEXT,
  CATEGORY_LABELS,
  LAYER_COLOURS,
  LAYER_LABELS,
  useMetamodel,
} from './useMetamodel'
import { useMetamodelRules } from './useMetamodelRules'

const route = useRoute()
const router = useRouter()
const metamodel = useMetamodel()
const rules = useMetamodelRules()

/** A query parameter is `string | string[] | null`; only one value means anything here. */
function one(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

// Both are passed on as they stand: the API knows the enums and refuses
// anything else, and its refusal is the message the user should read.
const source = computed(() => one(route.query.source) as ElementType | '')
const relation = computed(() => one(route.query.relation) as RelationshipType | '')

/** The rows to show: every target type, or only those the chosen relation reaches. */
const rows = computed(() =>
  rules.rules.value.filter(
    (rule) => !relation.value || rule.relationships.includes(relation.value),
  ),
)

const total = computed(() => rules.rules.value.length)

onMounted(async () => {
  await metamodel.load()
})

watch(
  source,
  async (chosen) => {
    if (!chosen) {
      rules.clear()
      return
    }
    await rules.load(chosen)
  },
  { immediate: true },
)

/**
 * Ask a new question by changing the URL.
 *
 * Changing the source type is a step of an exploration, so it is pushed and
 * the back button undoes it; narrowing to one relationship only replaces, or a
 * handful of filter changes would bury the previous type in the history.
 */
function ask(changes: Record<string, string | undefined>, step = false): void {
  const query = { ...route.query, ...changes }
  void (step ? router.push({ query }) : router.replace({ query }))
}

function choose(type: string): void {
  ask({ source: type }, true)
}
</script>

<template>
  <section class="metamodel">
    <header>
      <h2>Métamodèle</h2>
      <p class="hint">
        Les types d'éléments et de relations d'ArchiMate 3.2, tels que l'API les applique.
      </p>
    </header>

    <p v-if="metamodel.error.value" class="banner banner--error" role="alert">
      {{ metamodel.error.value }}
    </p>

    <section class="panel" aria-labelledby="metamodel-types">
      <h3 id="metamodel-types">Types d'éléments</h3>
      <p class="hint">
        Un clic sur un type montre, plus bas, tout ce qu'il peut relier.
      </p>

      <div v-for="group in metamodel.byLayer.value" :key="group.layer" class="layer">
        <h4>
          {{ LAYER_LABELS[group.layer] }}
          <span class="count">{{ group.types.length }}</span>
        </h4>
        <ul class="chips">
          <li v-for="type in group.types" :key="type.value">
            <button
              type="button"
              class="chip"
              :style="{ background: LAYER_COLOURS[group.layer], color: BOX_TEXT }"
              :aria-pressed="source === type.value"
              :title="ASPECT_LABELS[type.aspect]"
              @click="choose(type.value)"
            >
              {{ type.label }}
            </button>
          </li>
        </ul>
      </div>
    </section>

    <section class="panel" aria-labelledby="metamodel-relations">
      <h3 id="metamodel-relations">Relations</h3>
      <p class="hint">
        De la plus structurante à la plus faible — l'ordre dans lequel une relation
        indirecte est dérivée d'une chaîne, qui en garde le maillon le plus faible.
      </p>

      <div class="scroller">
        <table aria-label="Les relations du métamodèle">
          <thead>
            <tr>
              <th scope="col">Relation</th>
              <th scope="col">Famille</th>
              <th scope="col">Force</th>
              <th scope="col">Sens de l'impact</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="type in metamodel.byStrength.value" :key="type.value">
              <th scope="row">{{ RELATIONSHIP_LABELS[type.value] }}</th>
              <td>{{ CATEGORY_LABELS[type.category] }}</td>
              <td>{{ type.strength }}</td>
              <td>
                {{
                  type.impact_follows_direction
                    ? 'De la source vers la cible'
                    : 'De la cible vers la source'
                }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel" aria-labelledby="metamodel-rules">
      <h3 id="metamodel-rules">Règles d'association</h3>

      <p v-if="!source" class="hint">
        Choisis un type ci-dessus pour voir ce qu'il peut relier, et comment.
      </p>

      <template v-else>
        <div class="field">
          <label for="metamodel-relation">Relation</label>
          <select
            id="metamodel-relation"
            :value="relation"
            @change="ask({ relation: ($event.target as HTMLSelectElement).value || undefined })"
          >
            <option value="">Toutes les relations</option>
            <option
              v-for="type in metamodel.byStrength.value"
              :key="type.value"
              :value="type.value"
            >
              {{ RELATIONSHIP_LABELS[type.value] }}
            </option>
          </select>
        </div>

        <p v-if="rules.status.value === 'error'" class="banner banner--error" role="alert">
          {{ rules.error.value }}
        </p>

        <p v-else-if="rules.status.value === 'loading'" class="hint">Chargement des règles…</p>

        <template v-else>
          <p class="hint">
            Depuis « {{ metamodel.labelOf(source) }} » : {{ rows.length }} type{{
              rows.length > 1 ? 's' : ''
            }}
            sur {{ total }}<template v-if="relation">
              acceptent « {{ RELATIONSHIP_LABELS[relation] }} »</template
            ><template v-else> peuvent être reliés</template>.
          </p>

          <div class="scroller">
            <table :aria-label="`Règles depuis ${metamodel.labelOf(source)}`">
              <thead>
                <tr>
                  <th scope="col">Cible</th>
                  <th scope="col">Relations permises</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="rule in rows" :key="rule.target">
                  <th scope="row">
                    <button type="button" class="link" @click="choose(rule.target)">
                      {{ metamodel.labelOf(rule.target) }}
                    </button>
                  </th>
                  <td>
                    <ul class="verbs">
                      <li v-for="permitted in rule.relationships" :key="permitted">
                        {{ RELATIONSHIP_LABELS[permitted] }}
                      </li>
                    </ul>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
      </template>
    </section>
  </section>
</template>

<style scoped>
.metamodel {
  display: grid;
  gap: 1.5rem;
}
header h2 {
  margin: 0 0 0.2rem;
}
.panel {
  display: grid;
  gap: 0.6rem;
}
.panel h3 {
  margin: 0;
}
.layer h4 {
  margin: 0.4rem 0 0.3rem;
  font-size: 0.9rem;
}
.count {
  margin-left: 0.4rem;
  font-weight: 400;
  opacity: 0.6;
}
.chips,
.verbs {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin: 0;
  padding: 0;
  list-style: none;
}
.chip {
  font: inherit;
  font-size: 0.85rem;
  padding: 0.2rem 0.55rem;
  border: 1px solid rgba(0, 0, 0, 0.25);
  border-radius: 999px;
  cursor: pointer;
}
.chip[aria-pressed='true'] {
  outline: 2px solid var(--text);
  outline-offset: 1px;
}
.verbs li {
  font-size: 0.85rem;
  padding: 0.1rem 0.5rem;
  border: 1px solid var(--border);
  border-radius: 999px;
}
.link {
  font: inherit;
  font-weight: 600;
  padding: 0;
  border: 0;
  background: none;
  color: inherit;
  text-align: left;
  text-decoration: underline;
  cursor: pointer;
}
.field {
  display: grid;
  gap: 0.25rem;
  justify-items: start;
}
label {
  font-size: 0.85rem;
  font-weight: 600;
}
select {
  font: inherit;
  padding: 0.4rem 0.6rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: inherit;
  color: inherit;
}
/* A wide table scrolls inside its own box; the page never scrolls sideways. */
.scroller {
  overflow-x: auto;
}
table {
  border-collapse: collapse;
  width: 100%;
}
th,
td {
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid var(--border);
  text-align: left;
  vertical-align: top;
}
thead th {
  font-size: 0.85rem;
  opacity: 0.75;
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
