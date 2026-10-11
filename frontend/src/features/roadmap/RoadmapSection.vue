<script setup lang="ts">
// The `/analyse/feuille-de-route` section: the roadmap on top, the comparison of
// two plateaus below. The day read on (`?asof=`) is the shell's; here it only
// becomes a vertical line, because a roadmap shows every date at once — it does
// not filter by it.
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'

import RoadmapChart from '../../components/RoadmapChart.vue'
import { useAsOf } from '../../lib/asof'
import GapPanel from './GapPanel.vue'
import { useRoadmap } from './useRoadmap'

const router = useRouter()
const { asOf } = useAsOf()
const roadmap = useRoadmap()

// With no date anywhere the server resolves no window and every item is "non daté":
// the chart needs a window to be built, and draws none of it.
const NO_WINDOW = { from: '2000-01-01', to: '2000-01-01' }

onMounted(() => roadmap.load())

function open(id: string): void {
  void router.push({ name: 'elements', query: { element: id } })
}
</script>

<template>
  <section class="roadmap">
    <header>
      <h2>Feuille de route</h2>
      <p class="hint">
        Plateaux, work packages et livrables dans le temps, et l'écart entre deux plateaux.
      </p>
    </header>

    <p v-if="roadmap.status.value === 'error'" class="banner banner--error" role="alert">
      {{ roadmap.error.value }}
    </p>
    <p v-else-if="roadmap.status.value === 'loading'" class="hint">
      Chargement de la feuille de route…
    </p>

    <template v-else-if="roadmap.roadmap.value">
      <RoadmapChart
        v-if="roadmap.window.value"
        :items="roadmap.roadmap.value.items"
        :window="roadmap.window.value"
        :as-of="asOf"
        @open="open"
      />
      <template v-else>
        <p class="hint">
          Aucune date saisie : donne une date de début (<code>valid_from</code>) ou de fin
          (<code>valid_until</code>) à un plateau, un work package ou un livrable pour le voir ici.
        </p>
        <RoadmapChart :items="roadmap.roadmap.value.items" :window="NO_WINDOW" @open="open" />
      </template>
    </template>

    <GapPanel />
  </section>
</template>

<style scoped>
.roadmap {
  display: grid;
  gap: 1.5rem;
}
header h2 {
  margin: 0 0 0.2rem;
}
.hint {
  margin: 0;
  opacity: 0.75;
}
.banner--error {
  margin: 0;
  padding: 0.5rem 0.8rem;
  border: 1px solid currentColor;
  border-radius: 8px;
}
</style>
