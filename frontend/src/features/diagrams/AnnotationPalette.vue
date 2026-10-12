<script setup lang="ts">
// The two things a diagram can draw that are not elements: a note and a group.
// Neither is a fact — a group says "drawn together", never "composed of".
const emit = defineEmits<{ 'add-note': []; 'add-group': [] }>()
</script>

<template>
  <section class="annotations" aria-label="Annotations">
    <h3>Annotations</h3>
    <button type="button" @click="emit('add-note')">Ajouter une note</button>
    <button type="button" @click="emit('add-group')">Ajouter un groupe</button>
  </section>
</template>

<style scoped>
.annotations {
  display: grid;
  gap: 0.35rem;
}
.annotations h3 {
  margin: 0;
  font-size: 0.9rem;
}
</style>
