<script setup lang="ts">
// The reader mode: the model read, never written (docs/adr/0044). Same origin,
// same login, same endpoints as the rest of the SPA, so there is no second
// surface to protect — only a lighter layout around the same reads.
import { HOME } from '../../router/sections'
</script>

<template>
  <section class="view">
    <nav class="view__bar" aria-label="Consultation">
      <RouterLink :to="{ name: 'view-home' }">Consultation</RouterLink>
      <RouterLink :to="HOME">Ouvrir l'application complète</RouterLink>
    </nav>
    <RouterView />
  </section>
</template>

<style scoped>
.view__bar {
  display: flex;
  flex-wrap: wrap;
  gap: 1.25rem;
  margin-bottom: 1.25rem;
}
</style>
