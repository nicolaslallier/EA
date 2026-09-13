<script setup lang="ts">
// What a login that went wrong leaves on screen — the callback that could not
// finish, and the redirect to Keycloak that could not even start. Without it
// the second is a blank page: the likeliest first failure of a deployment
// (Keycloak unreachable, a page served over plain http) with no symptom at all.
//
// The reason is in the console, where the router logged it; *Réessayer* is a
// full page load, so the gate runs again from scratch.
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { safeReturnPath } from '../lib/auth'

const { message = 'La connexion n’a pas pu démarrer — la raison est dans la console du navigateur.' } =
  defineProps<{ message?: string }>()

const route = useRoute()
const retry = computed(() => safeReturnPath(route.query.returnTo))
</script>

<template>
  <p role="alert">
    {{ message }} <a :href="retry">Réessayer</a>
  </p>
</template>
