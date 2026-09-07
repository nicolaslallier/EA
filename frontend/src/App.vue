<script setup lang="ts">
import BackendStatus from './components/BackendStatus.vue'
import ElementCatalogue from './features/elements/ElementCatalogue.vue'
</script>

<template>
  <main>
    <header class="app-header">
      <h1>EA — Enterprise Architecture</h1>
      <BackendStatus />
    </header>
    <ElementCatalogue />
  </main>
</template>

<style scoped>
.app-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 1.5rem;
}
.app-header h1 {
  margin: 0;
}
</style>
