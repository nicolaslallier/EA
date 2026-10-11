<script setup lang="ts">
// The `/analyse/feuille-de-route` section. For now the comparison of two
// plateaus; the roadmap itself is drawn above it from the next batch.
import GapPanel from './GapPanel.vue'
</script>

<template>
  <section class="roadmap">
    <header>
      <h2>Feuille de route</h2>
      <p class="hint">
        Plateaux, work packages et livrables dans le temps, et l'écart entre deux plateaux.
      </p>
    </header>
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
</style>
