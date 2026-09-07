<script setup lang="ts">
// The `/relations` section: pick an element, then work on its relations.
//
// Associating is always done from one element — a flat list of every link in
// the repository would mean choosing both ends blind. So this screen only asks
// *which* element, and hands the rest to the same panel the catalogue opens
// from a row. See docs/adr/0009.
import { onMounted, ref } from 'vue'

import { messageOf } from '../../lib/api'
import type { ElementRead } from '../elements/useElementCatalogue'
import { useElementCatalogue } from '../elements/useElementCatalogue'
import RelationshipPanel from './RelationshipPanel.vue'

const catalogue = useElementCatalogue()
const selectedId = ref('')
const selected = ref<ElementRead | null>(null)
const failure = ref('')

onMounted(async () => {
  await catalogue.load()
})

function onSelect(): void {
  selected.value =
    catalogue.items.value.find((element) => element.id === selectedId.value) ?? null
}

async function onSearch(): Promise<void> {
  failure.value = ''
  try {
    await catalogue.search()
  } catch (caught) {
    failure.value = messageOf(caught)
  }
  // The chosen element may not be on the new page; the panel would then be
  // showing relations the picker no longer names.
  onSelect()
}
</script>

<template>
  <section class="section">
    <header>
      <h2>Relations</h2>
      <p class="hint">Relier les éléments, sous les règles du métamodèle.</p>
    </header>

    <p v-if="catalogue.status.value === 'error'" class="banner banner--error" role="alert">
      {{ catalogue.error.value }}
    </p>
    <p v-else-if="failure" class="banner banner--error" role="alert">{{ failure }}</p>

    <form class="picker" role="search" aria-label="Choisir un élément"
          @submit.prevent="onSearch">
      <div class="field">
        <label for="relations-search">Rechercher</label>
        <input id="relations-search" v-model="catalogue.filters.search" type="search"
               placeholder="Un fragment de nom" />
      </div>
      <div class="field">
        <label for="relations-element">Élément</label>
        <select id="relations-element" v-model="selectedId" @change="onSelect">
          <option value="">Choisis un élément…</option>
          <option v-for="element in catalogue.items.value" :key="element.id" :value="element.id">
            {{ element.name }}
          </option>
        </select>
      </div>
      <button type="submit">Filtrer</button>
    </form>

    <p v-if="!selected" class="hint">
      Choisis un élément pour voir ses relations et en ajouter.
    </p>

    <RelationshipPanel
      v-else
      :key="selected.id"
      :element="selected"
      @close="((selected = null), (selectedId = ''))"
    />
  </section>
</template>

<style scoped>
.section {
  display: grid;
  gap: 1rem;
}
header h2 {
  margin: 0 0 0.2rem;
}
.picker {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  gap: 0.6rem;
}
.field {
  display: grid;
  gap: 0.25rem;
}
label {
  font-size: 0.85rem;
  font-weight: 600;
}
input,
select {
  font: inherit;
  padding: 0.4rem 0.6rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: inherit;
  color: inherit;
}
button {
  font: inherit;
  padding: 0.4rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
}
.hint {
  margin: 0;
  opacity: 0.75;
}
.banner {
  margin: 0;
  padding: 0.6rem 0.9rem;
  border: 1px solid #c62828;
  border-radius: 6px;
  color: #c62828;
}
</style>
