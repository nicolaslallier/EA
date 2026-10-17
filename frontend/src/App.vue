<script setup lang="ts">
// The shell: one top bar (the brand, the section menu, the status) and,
// below it, whatever the router mounts. It holds no screen of its own —
// every section lives behind a route.
import { useRoute } from 'vue-router'

import AppNav from './components/AppNav.vue'
import AsOfPicker from './components/AsOfPicker.vue'
import BackendStatus from './components/BackendStatus.vue'
import ThemeToggle from './components/ThemeToggle.vue'
import UserBadge from './components/UserBadge.vue'
import { useAsOf } from './lib/asof'
import { HOME } from './router/sections'

const { asOf } = useAsOf()
const route = useRoute()
</script>

<template>
  <div class="app">
    <header class="app__bar">
      <RouterLink class="app__brand" :to="HOME" title="EA — Enterprise Architecture">EA</RouterLink>
      <AppNav v-if="!route.meta.reader" />
      <div class="app__status">
        <AsOfPicker />
        <BackendStatus />
        <UserBadge />
        <ThemeToggle />
      </div>
    </header>

    <!-- The whole browser width, for every section: a table or a canvas has
         no use for a reading column. -->
    <main class="app__main">
      <p v-if="asOf" class="app__asof" role="status">
        Vue au {{ asOf }} — le catalogue est filtré à cette date.
      </p>
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.app__bar {
  position: relative;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem 1.5rem;
  padding: 0 1.5rem;
  min-height: 3.5rem;
  color: var(--bar-text);
  background: var(--bar-bg);
}

.app__brand {
  font-size: 1.35rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: inherit;
  text-decoration: none;
}

/* The status components draw their borders with the page tokens; on the
   blue band they take the bar's. */
.app__status {
  --text: var(--bar-text);
  --border: var(--bar-border);
  /* Light controls (the date picker's icon) on a dark band. */
  color-scheme: dark;

  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
  margin-left: auto;
  padding: 0.4rem 0;
}

.app__status :deep(p) {
  margin: 0;
}

.app__main {
  padding: 1.75rem 1.5rem 3rem;
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
  .app__bar {
    padding: 0 1rem;
  }
  .app__main {
    padding: 1.25rem 1rem 2rem;
  }
}
</style>
