<script setup lang="ts">
import BackendStatus from './components/BackendStatus.vue'
</script>

<template>
  <main>
    <h1>EA — Enterprise Architecture</h1>
    <p>Scaffolding local. Le backend est servi par <code>make run-be</code>, ce SPA par <code>make run-fe</code>.</p>
    <BackendStatus />
  </main>
</template>
