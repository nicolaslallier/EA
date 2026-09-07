<script setup lang="ts">
// The section menu. It renders `menu()` and nothing else, so a new section is
// declared once in `router/sections.ts` and appears here on its own.
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { menu } from '../router/sections'

const entries = menu()
const route = useRoute()

/** Narrow screens collapse the menu behind a toggle; wide ones ignore this. */
const open = ref(false)

// Opening a section on a phone must put the screen in front of the user, not
// leave the menu covering it.
watch(() => route.fullPath, () => {
  open.value = false
})
</script>

<template>
  <nav class="nav" aria-label="Sections">
    <button
      class="nav__toggle"
      type="button"
      aria-controls="nav-sections"
      :aria-expanded="open"
      @click="open = !open"
    >
      Menu
    </button>

    <div id="nav-sections" class="nav__sections" :class="{ 'nav__sections--open': open }">
      <section v-for="entry in entries" :key="entry.group.id" class="nav__group">
        <h2 class="nav__heading">{{ entry.group.label }}</h2>
        <ul class="nav__list">
          <li v-for="section in entry.sections" :key="section.path">
            <RouterLink
              v-if="section.view"
              class="nav__item"
              :to="section.path"
              :title="section.summary"
            >
              {{ section.label }}
            </RouterLink>
            <span v-else class="nav__item nav__item--upcoming" :title="section.summary" aria-disabled="true">
              {{ section.label }}
              <span class="nav__badge">à venir</span>
            </span>
          </li>
        </ul>
      </section>
    </div>
  </nav>
</template>

<style scoped>
.nav {
  --nav-width: 15rem;
}

.nav__toggle {
  display: none;
  font: inherit;
  color: inherit;
  background: none;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.5rem 0.9rem;
  cursor: pointer;
}

.nav__sections {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
  width: var(--nav-width);
}

.nav__group {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.nav__heading {
  margin: 0 0 0.15rem;
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  opacity: 0.6;
}

.nav__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.nav__item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.45rem 0.7rem;
  border-radius: 8px;
  border: 1px solid transparent;
  color: inherit;
  text-decoration: none;
}

a.nav__item:hover,
a.nav__item:focus-visible {
  border-color: var(--border);
}

a.nav__item[aria-current='page'] {
  border-color: var(--border);
  background: color-mix(in srgb, var(--text) 8%, transparent);
  font-weight: 600;
}

.nav__item--upcoming {
  opacity: 0.55;
  cursor: not-allowed;
}

.nav__badge {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.05rem 0.45rem;
  white-space: nowrap;
}

@media (max-width: 48rem) {
  .nav {
    width: 100%;
  }
  .nav__toggle {
    display: block;
  }
  .nav__sections {
    display: none;
    width: 100%;
    margin-top: 1rem;
  }
  .nav__sections--open {
    display: flex;
  }
}
</style>
