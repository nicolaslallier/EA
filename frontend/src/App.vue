<script setup lang="ts">
// The shell: a header, the section menu, and whatever the router mounts.
// It holds no screen of its own — every section lives behind a route.
import { useRoute } from 'vue-router'

import AppNav from './components/AppNav.vue'
import AsOfPicker from './components/AsOfPicker.vue'
import BackendStatus from './components/BackendStatus.vue'
import UserBadge from './components/UserBadge.vue'
import { useAsOf } from './lib/asof'
import { HOME } from './router/sections'

const { asOf } = useAsOf()
const route = useRoute()
</script>

<template>
  <div class="app">
    <header class="app__header">
      <RouterLink class="app__brand" :to="HOME">EA — Enterprise Architecture</RouterLink>
      <div class="app__status">
        <AsOfPicker />
        <BackendStatus />
        <UserBadge />
      </div>
    </header>

    <div class="app__body">
      <AppNav v-if="!route.meta.reader" />
      <main class="app__main">
        <p v-if="asOf" class="app__asof" role="status">
          Vue au {{ asOf }} — le catalogue est filtré à cette date.
        </p>
        <RouterView />
      </main>
    </div>
  </div>
</template>

<style scoped>
/* The whole browser width, for every section: a table or a canvas has no use
   for a reading column. */
.app {
  padding: 2rem 1.5rem 3rem;
}

.app__header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding-bottom: 1.25rem;
  border-bottom: 1px solid var(--border);
}

.app__brand {
  font-size: 1.35rem;
  font-weight: 700;
  color: inherit;
  text-decoration: none;
}

.app__status {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.app__body {
  display: flex;
  align-items: flex-start;
  gap: 2.5rem;
  padding-top: 1.75rem;
}

.app__main {
  flex: 1;
  min-width: 0;
}

.app__asof {
  margin: 0 0 1rem;
  padding: 0.5rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 0.9rem;
}

@media (max-width: 48rem) {
  .app__body {
    flex-direction: column;
    gap: 1.5rem;
  }
  .app__main {
    width: 100%;
  }
}
</style>
