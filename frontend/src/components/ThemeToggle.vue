<script setup lang="ts">
// Light or dark, chosen by the person and remembered by this browser.
// The name stays "Thème sombre" and `aria-pressed` carries the state: a name
// that flipped with it would be read "Thème clair, enfoncé" in dark.
import { computed, ref } from 'vue'

import { setTheme, storedTheme, type Theme } from '../lib/theme'

const applied = document.documentElement.dataset.theme
const theme = ref<Theme>(applied === 'dark' || applied === 'light' ? applied : storedTheme())
const dark = computed(() => theme.value === 'dark')

function toggle(): void {
  theme.value = dark.value ? 'light' : 'dark'
  setTheme(theme.value)
}
</script>

<template>
  <button
    type="button"
    class="theme"
    aria-label="Thème sombre"
    :aria-pressed="dark"
    title="Thème sombre"
    @click="toggle"
  >
    <span aria-hidden="true">{{ dark ? '☀' : '☾' }}</span>
  </button>
</template>

<style scoped>
.theme {
  font: inherit;
  font-size: 1.1rem;
  line-height: 1;
  color: inherit;
  background: none;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.35rem 0.55rem;
  cursor: pointer;
}
</style>
