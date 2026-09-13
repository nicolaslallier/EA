<script setup lang="ts">
// Every element, grouped by layer, ready to be dragged onto the diagram.
//
// A row carries only the element's id in its `dataTransfer`; the section looks
// the element up in the list it handed here. An element already on the
// diagram is dimmed and cannot be dragged again — a view shows it once.
import { computed, onBeforeUnmount } from 'vue'

import { debounce } from '../../lib/debounce'
import { LAYER_COLOURS, LAYER_LABELS, type Layer } from '../metamodel/useMetamodel'
import { ELEMENT_DRAG_TYPE, type ElementRead } from './useDiagram'

/** As the relations panel: one request per pause in the typing, not per key. */
const SEARCH_PAUSE_MS = 250

const props = defineProps<{
  items: ElementRead[]
  total: number
  placed: Set<string>
  error: string
  typeLabel: (value: string) => string
}>()

const emit = defineEmits<{ search: [term: string]; add: [element: ElementRead] }>()

/** The layers in the order ArchiMate lists them — the order of `LAYER_LABELS`. */
const groups = computed(() =>
  (Object.keys(LAYER_LABELS) as Layer[])
    .map((layer) => ({ layer, elements: props.items.filter((item) => item.layer === layer) }))
    .filter((group) => group.elements.length > 0),
)

const search = debounce((term: string) => emit('search', term), SEARCH_PAUSE_MS)
onBeforeUnmount(search.cancel)

function onDragStart(event: DragEvent, element: ElementRead): void {
  if (!event.dataTransfer) {
    return
  }
  event.dataTransfer.setData(ELEMENT_DRAG_TYPE, element.id)
  event.dataTransfer.effectAllowed = 'copy'
}
</script>

<template>
  <section class="palette" aria-label="Palette des éléments">
    <label for="palette-search">Rechercher un élément</label>
    <input id="palette-search" type="search" placeholder="Un fragment de nom"
           @input="search.call(($event.target as HTMLInputElement).value)" />

    <p v-if="error" class="banner" role="alert">{{ error }}</p>
    <p v-if="total > items.length" class="hint">
      {{ items.length }} sur {{ total }} — affine la recherche.
    </p>

    <div v-for="group in groups" :key="group.layer" class="palette__group">
      <h4>
        <span class="palette__swatch" :style="{ background: LAYER_COLOURS[group.layer] }"
              aria-hidden="true" />
        {{ LAYER_LABELS[group.layer] }}
      </h4>
      <ul>
        <li
          v-for="element in group.elements"
          :key="element.id"
          :draggable="placed.has(element.id) ? 'false' : 'true'"
          :aria-disabled="placed.has(element.id)"
          :class="{ 'palette__item--placed': placed.has(element.id) }"
          :title="placed.has(element.id) ? 'Déjà sur le diagramme' : 'Glisse-le sur le diagramme'"
          @dragstart="onDragStart($event, element)"
        >
          <span class="palette__name">{{ element.name }}</span>
          <span class="palette__type">{{ typeLabel(element.element_type) }}</span>
          <!-- The keyboard's way onto the diagram, where a drag is not an option. -->
          <button v-if="!placed.has(element.id)" type="button" class="palette__add"
                  :aria-label="`Ajouter « ${element.name} » au diagramme`"
                  @click="emit('add', element)">+</button>
        </li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
.palette {
  display: grid;
  gap: 0.4rem;
  align-content: start;
  max-height: 44rem;
  overflow: auto;
}
label {
  font-size: 0.85rem;
  font-weight: 600;
}
input {
  font: inherit;
  padding: 0.35rem 0.5rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: inherit;
  color: inherit;
}
h4 {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  margin: 0.5rem 0 0.2rem;
  font-size: 0.8rem;
}
.palette__swatch {
  width: 0.7rem;
  height: 0.7rem;
  border: 1px solid #6f6b78;
  border-radius: 3px;
}
ul {
  display: grid;
  gap: 0.2rem;
  margin: 0;
  padding: 0;
  list-style: none;
}
li {
  display: grid;
  grid-template-columns: 1fr auto;
  padding: 0.3rem 0.4rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: grab;
  font-size: 0.85rem;
}
.palette__item--placed {
  opacity: 0.45;
  cursor: default;
}
.palette__name {
  font-weight: 600;
}
.palette__type {
  grid-column: 1;
  font-size: 0.75rem;
  opacity: 0.75;
}
.palette__add {
  grid-column: 2;
  grid-row: 1 / span 2;
  align-self: center;
  font: inherit;
  padding: 0 0.45rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.hint {
  margin: 0;
  font-size: 0.8rem;
  opacity: 0.75;
}
.banner {
  margin: 0;
  padding: 0.4rem 0.6rem;
  border: 1px solid #c62828;
  border-radius: 6px;
  color: #c62828;
  font-size: 0.85rem;
}
</style>
