<script setup lang="ts">
// Who is on screen, and the one control that ends the session (docs/adr/0032).
import { onMounted } from 'vue'

import { signOut } from '../lib/auth'
import { useMe } from '../lib/me'

const me = useMe()

onMounted(() => {
  void me.load()
})

function onSignOut(): void {
  void signOut()
}
</script>

<template>
  <p v-if="me.me.value" class="badge">
    {{ me.me.value.username }}
    <button type="button" class="secondary" @click="onSignOut">Déconnexion</button>
  </p>
</template>

<style scoped>
.badge {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  font: inherit;
}
button {
  font: inherit;
  padding: 0.4rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
}
.secondary {
  background: transparent;
}
</style>
