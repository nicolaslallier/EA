<script setup lang="ts">
// "Comparer deux plateaux": the two plateaus live in the URL (`?a=`, `?b=`), so
// a comparison is a link to send and the back button walks it back. The server
// computes the difference; this panel only reads it out.
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useMe } from '../../lib/me'
import type { ElementRead } from './useGap'
import { useGap } from './useGap'

const route = useRoute()
const router = useRouter()
const { canWrite } = useMe()
const gap = useGap()

function one(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

const a = computed(() => one(route.query.a))
const b = computed(() => one(route.query.b))

onMounted(() => gap.loadPlateaus())

watch(
  [a, b],
  async ([from, to]) => {
    if (from && to) {
      await gap.compare(from, to)
    } else {
      gap.clear()
    }
  },
  { immediate: true },
)

function choose(which: 'a' | 'b', event: Event): void {
  const chosen = (event.target as HTMLSelectElement).value
  void router.replace({ query: { ...route.query, [which]: chosen || undefined } })
}

const existing = computed(() => gap.comparison.value?.gaps ?? [])

const groups = computed<{ id: string; title: string; items: ElementRead[] }[]>(() => {
  const found = gap.comparison.value
  return found
    ? [
        { id: 'out', title: 'Sorties', items: found.only_in_a },
        { id: 'in', title: 'Arrivées', items: found.only_in_b },
        { id: 'same', title: 'Communs', items: found.common },
      ]
    : []
})
</script>

<template>
  <section class="gap" aria-labelledby="gap-title">
    <header>
      <h3 id="gap-title">Comparer deux plateaux</h3>
      <p class="hint">Ce qui sort, ce qui arrive et ce qui reste entre deux états du SI.</p>
    </header>

    <div class="controls">
      <div class="field">
        <label for="gap-a">Plateau de départ</label>
        <select id="gap-a" :value="a" @change="choose('a', $event)">
          <option value="">Choisis un plateau…</option>
          <option v-for="plateau in gap.plateaus.value" :key="plateau.id" :value="plateau.id">
            {{ plateau.name }}
          </option>
        </select>
      </div>
      <div class="field">
        <label for="gap-b">Plateau d'arrivée</label>
        <select id="gap-b" :value="b" @change="choose('b', $event)">
          <option value="">Choisis un plateau…</option>
          <option v-for="plateau in gap.plateaus.value" :key="plateau.id" :value="plateau.id">
            {{ plateau.name }}
          </option>
        </select>
      </div>
    </div>

    <p v-if="!a || !b" class="hint">Choisis deux plateaux pour voir ce qui les sépare.</p>
    <p v-else-if="gap.status.value === 'error'" class="banner banner--error" role="alert">
      {{ gap.error.value }}
    </p>
    <p v-else-if="gap.status.value === 'loading'" class="hint">Comparaison en cours…</p>

    <template v-else-if="gap.comparison.value">
      <div class="columns">
        <section v-for="group in groups" :key="group.id" :aria-labelledby="`gap-${group.id}`">
          <h4 :id="`gap-${group.id}`">{{ group.title }} ({{ group.items.length }})</h4>
          <ul v-if="group.items.length">
            <li v-for="item in group.items" :key="item.id">{{ item.name }}</li>
          </ul>
          <p v-else class="hint">Aucun élément.</p>
        </section>
      </div>

      <section v-if="existing.length" aria-labelledby="gap-existing">
        <h4 id="gap-existing">Écarts déjà décrits</h4>
        <ul>
          <li v-for="item in existing" :key="item.id">{{ item.name }}</li>
        </ul>
      </section>
      <div v-else-if="canWrite" class="actions">
        <button type="button" :disabled="gap.creating.value" @click="gap.createGap(a, b)">
          Créer l'écart
        </button>
        <p v-if="gap.createError.value" class="banner banner--error" role="alert">
          {{ gap.createError.value }}
        </p>
      </div>
    </template>
  </section>
</template>

<style scoped>
.gap {
  display: grid;
  gap: 0.9rem;
}
.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
}
.field {
  display: grid;
  gap: 0.3rem;
}
select {
  font: inherit;
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: inherit;
  color: inherit;
}
.columns {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
  gap: 1rem;
}
h3,
h4 {
  margin: 0 0 0.3rem;
}
.hint {
  margin: 0;
  font-size: 0.9rem;
  opacity: 0.75;
}
.banner--error {
  margin: 0;
  padding: 0.5rem 0.8rem;
  border: 1px solid currentColor;
  border-radius: 8px;
}
</style>
