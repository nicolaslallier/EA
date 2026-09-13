<script setup lang="ts">
// Where Keycloak sends the browser back. It finishes the login and replaces
// itself with the page the user was going to, so Back never lands here again.
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { completeSignIn } from '../lib/auth'
import LoginFailed from './LoginFailed.vue'

const router = useRouter()
const failed = ref(false)

onMounted(async () => {
  try {
    await router.replace(await completeSignIn())
  } catch {
    failed.value = true
  }
})
</script>

<template>
  <LoginFailed v-if="failed" message="La connexion a échoué." />
  <p v-else>Connexion…</p>
</template>
