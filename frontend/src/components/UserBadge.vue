<script setup lang="ts">
// Who is on screen, and the one control that ends the session (docs/adr/0032).
import { watch } from 'vue'
import { useRoute } from 'vue-router'

import { signOut } from '../lib/auth'
import { useMe } from '../lib/me'

const me = useMe()
const route = useRoute()

// Not on mount: the shell mounts on /auth/callback too, before the login has
// stored a token, and a /me sent then is a 401 nothing retries. A resolved,
// non-public route is one the router's gate let through with a token.
watch(
  () => route.matched.length > 0 && !route.meta.public,
  (signedIn) => {
    if (signedIn) {
      void me.load()
    }
  },
  { immediate: true },
)

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
