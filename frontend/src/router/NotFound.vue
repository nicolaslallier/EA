<script setup lang="ts">
// The screen behind every unknown URL. It lives next to the router because the
// router is the only thing that mounts it.
import { HOME } from './sections'
</script>

<template>
  <section class="not-found">
    <h2>Section introuvable</h2>
    <p>
      Cette adresse ne correspond à aucune section — soit elle a changé, soit la section n’est pas
      encore construite.
    </p>
    <RouterLink :to="HOME">Revenir au catalogue</RouterLink>
  </section>
</template>

<style scoped>
.not-found {
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1.5rem;
}
.not-found h2 {
  margin-top: 0;
}
</style>
