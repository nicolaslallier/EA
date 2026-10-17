<script setup lang="ts">
// The section menu. It renders `menu()` and nothing else, so a new section is
// declared once in `router/sections.ts` and appears here on its own.
//
// One button per group in the top bar, each opening a dropdown of its sections
// (docs/adr/0075). The menu is a prop only so a test can hand it a catalogue of
// its own: a section still to be built gets a badge instead of a link, and that
// has to stay covered even on the day every declared section has a screen.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { menu, type MenuEntry } from '../router/sections'

withDefaults(defineProps<{ entries?: MenuEntry[] }>(), { entries: () => menu() })

const route = useRoute()
const root = ref<HTMLElement | null>(null)

/** Narrow screens collapse the groups behind a toggle; wide ones ignore this. */
const open = ref(false)
/** The one group whose dropdown is showing, if any. */
const openGroup = ref<string | null>(null)

function toggleGroup(id: string): void {
  openGroup.value = openGroup.value === id ? null : id
}

/** The group holding the screen on show — `/analyse/impact` included. */
function isCurrent(entry: MenuEntry): boolean {
  return entry.sections.some(
    (section) => route.path === section.path || route.path.startsWith(`${section.path}/`),
  )
}

/** Folds everything: the open dropdown and, on a phone, the whole menu. */
function close(): void {
  open.value = false
  openGroup.value = null
}

function outside(target: EventTarget | null): boolean {
  return root.value !== null && !root.value.contains(target as Node)
}

function onEscape(): void {
  const id = openGroup.value
  const folded = open.value
  if (id === null && !folded) return
  close()
  // Give the focus back to the control that opened what just closed.
  document.getElementById(folded ? 'nav-toggle' : `nav-group-${id}`)?.focus()
}

function onPointerDown(event: PointerEvent): void {
  if (outside(event.target)) close()
}

// Tabbing out of an open dropdown must not leave it over the screen. A null
// `relatedTarget` (Safari does not focus a clicked link) is left to the
// pointer handler, or the dropdown would vanish under the click.
function onFocusOut(event: FocusEvent): void {
  if (event.relatedTarget !== null && outside(event.relatedTarget)) close()
}

onMounted(() => document.addEventListener('pointerdown', onPointerDown))
onBeforeUnmount(() => document.removeEventListener('pointerdown', onPointerDown))

// Opening a section must put the screen in front of the user, not leave a
// dropdown — or, on a phone, the whole menu — covering it.
// Clicking the page already open is no navigation, hence the click handler on
// each link as well.
watch(() => route.fullPath, close)
</script>

<template>
  <nav ref="root" class="nav" aria-label="Sections" @keydown.esc="onEscape" @focusout="onFocusOut">
    <button
      id="nav-toggle"
      class="nav__toggle"
      type="button"
      aria-controls="nav-sections"
      :aria-expanded="open"
      @click="open = !open"
    >
      ☰ Menu
    </button>

    <ul id="nav-sections" class="nav__groups" :class="{ 'nav__groups--open': open }">
      <li v-for="entry in entries" :key="entry.group.id" class="nav__group">
        <button
          :id="`nav-group-${entry.group.id}`"
          class="nav__trigger"
          :class="{ 'nav__trigger--current': isCurrent(entry) }"
          type="button"
          :aria-current="isCurrent(entry) ? 'true' : undefined"
          :aria-expanded="openGroup === entry.group.id"
          :aria-controls="`nav-panel-${entry.group.id}`"
          @click="toggleGroup(entry.group.id)"
        >
          <span>{{ entry.group.label }}</span>
          <span class="nav__caret" aria-hidden="true">▾</span>
        </button>

        <ul v-show="openGroup === entry.group.id" :id="`nav-panel-${entry.group.id}`" class="nav__panel">
          <li v-for="section in entry.sections" :key="section.path">
            <RouterLink v-if="section.view" class="nav__item" :to="section.path" @click="close">
              <span class="nav__label">{{ section.label }}</span>
              <span class="nav__summary">{{ section.summary }}</span>
            </RouterLink>
            <span v-else class="nav__item nav__item--upcoming" aria-disabled="true">
              <span class="nav__label">
                {{ section.label }}
                <span class="nav__badge">à venir</span>
              </span>
              <span class="nav__summary">{{ section.summary }}</span>
            </span>
          </li>
        </ul>
      </li>
    </ul>
  </nav>
</template>

<style scoped>
.nav {
  flex: 1;
}

.nav__toggle,
.nav__trigger {
  font: inherit;
  color: inherit;
  background: none;
  cursor: pointer;
}

.nav__toggle {
  display: none;
  border: 1px solid var(--bar-border);
  border-radius: 8px;
  padding: 0.4rem 0.8rem;
}

.nav__groups,
.nav__panel {
  list-style: none;
  margin: 0;
  padding: 0;
}

.nav__groups {
  display: flex;
  gap: 0.25rem;
}

.nav__group {
  position: relative;
}

.nav__trigger {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  padding: 1rem 0.8rem 0.85rem;
  border: none;
  /* The accent marker sits on this border, so every trigger reserves it. */
  border-bottom: 3px solid transparent;
  font-weight: 500;
}

.nav__trigger:hover,
.nav__trigger:focus-visible,
.nav__trigger[aria-expanded='true'] {
  background: var(--bar-hover);
}

.nav__trigger--current {
  border-bottom-color: var(--bar-accent);
  font-weight: 600;
}

.nav__caret {
  font-size: 0.75em;
  opacity: 0.8;
}

.nav__panel {
  position: absolute;
  top: 100%;
  left: 0;
  z-index: 50;
  min-width: 20rem;
  padding: 0.4rem;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0 0 10px 10px;
  box-shadow: 0 10px 24px rgb(15 23 42 / 18%);
  max-width: calc(100vw - 2rem);
}

/* The right-most groups open leftwards, or their dropdown leaves the window. */
.nav__group:nth-last-child(-n + 2) .nav__panel {
  left: auto;
  right: 0;
}

.nav__item {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  padding: 0.5rem 0.7rem;
  border-radius: 8px;
  color: inherit;
  text-decoration: none;
}

.nav__label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-weight: 500;
}

.nav__summary {
  font-size: 0.82rem;
  color: var(--muted);
}

a.nav__item:hover,
a.nav__item:focus-visible {
  background: color-mix(in srgb, var(--accent) 10%, transparent);
}

a.nav__item[aria-current='page'] {
  background: color-mix(in srgb, var(--accent) 14%, transparent);
}

a.nav__item[aria-current='page'] .nav__label {
  color: var(--accent);
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

/* Narrow screens: the groups stack under the bar, each dropdown inline. */
@media (max-width: 48rem) {
  .nav__toggle {
    display: block;
  }
  .nav__groups {
    display: none;
    position: absolute;
    top: 100%;
    left: 0;
    right: 0;
    z-index: 50;
    flex-direction: column;
    padding: 0.5rem 1rem 1rem;
    background: var(--bar-bg);
  }
  .nav__groups--open {
    display: flex;
  }
  .nav__trigger {
    width: 100%;
    padding: 0.7rem 0.5rem;
    border-bottom: none;
    border-left: 3px solid transparent;
  }
  .nav__trigger--current {
    border-left-color: var(--bar-accent);
  }
  .nav__panel,
  .nav__group:nth-last-child(-n + 2) .nav__panel {
    position: static;
    min-width: 0;
    border-radius: 8px;
    box-shadow: none;
  }
}
</style>
