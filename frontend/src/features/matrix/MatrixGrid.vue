<script setup lang="ts">
// The grid itself: names on both axes, one cell per ordered pair.
//
// Rows are sources, columns are targets; a link is drawn in the cell of its own
// direction and never mirrored. It fetches nothing and decides nothing: which
// pairs are blocked, and what a cell holds, comes in `matrix` from the API.
//
// One cell is in the tab order at a time (roving tabindex): the arrows move,
// Enter or Space opens the cell, Escape closes the panel. A cell is a real
// focusable element, not a painted pixel.
import { computed, nextTick, ref, useTemplateRef } from 'vue'

import { RELATIONSHIP_LABELS } from '../relationships/labels'
import { moveFocus, type Position } from './gridNavigation'
import type { MatrixRead } from './useMatrix'

const props = defineProps<{
  matrix: MatrixRead
  selected: { source: string; target: string } | null
}>()
const emit = defineEmits<{ open: [source: string, target: string]; close: [] }>()

const grid = useTemplateRef<HTMLElement>('grid')
const focus = ref<Position>({ row: 0, col: 0 })

const linked = computed(
  () => new Map(props.matrix.cells.map((cell) => [`${cell.source_id}|${cell.target_id}`, cell])),
)
const blocked = computed(
  () => new Set(props.matrix.blocked.map((pair) => `${pair.source_id}|${pair.target_id}`)),
)

const isEmpty = computed(() => props.matrix.rows.length === 0 || props.matrix.cols.length === 0)

function keyOf(row: number, col: number): string {
  return `${props.matrix.rows[row]?.id ?? ''}|${props.matrix.cols[col]?.id ?? ''}`
}

function linksIn(row: number, col: number) {
  return linked.value.get(keyOf(row, col))?.links ?? []
}

function isBlocked(row: number, col: number): boolean {
  return blocked.value.has(keyOf(row, col))
}

function isSelected(row: number, col: number): boolean {
  return props.selected !== null && keyOf(row, col) === `${props.selected.source}|${props.selected.target}`
}

function label(row: number, col: number): string {
  const count = linksIn(row, col).length
  const from = props.matrix.rows[row]?.name ?? ''
  const to = props.matrix.cols[col]?.name ?? ''
  return `${from} vers ${to} : ${count} lien${count > 1 ? 's' : ''}`
}

function open(row: number, col: number): void {
  const source = props.matrix.rows[row]
  const target = props.matrix.cols[col]
  focus.value = { row, col }
  if (!source || !target || isBlocked(row, col)) {
    return
  }
  emit('open', source.id, target.id)
}

async function onKeydown(event: KeyboardEvent, row: number, col: number): Promise<void> {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    open(row, col)
    return
  }
  if (event.key === 'Escape') {
    emit('close')
    return
  }
  const next = moveFocus({ row, col }, event.key, {
    rows: props.matrix.rows.length,
    cols: props.matrix.cols.length,
  })
  if (next.row === row && next.col === col) {
    return
  }
  event.preventDefault()
  focus.value = next
  await nextTick()
  grid.value
    ?.querySelector<HTMLElement>(`[data-r="${next.row}"][data-c="${next.col}"]`)
    ?.focus()
}
</script>

<template>
  <p v-if="isEmpty" class="hint">Aucun élément sur au moins un des deux axes : rien à croiser.</p>

  <div v-else class="matrix-scroll">
    <table ref="grid" class="matrix" role="grid" aria-label="Matrice de relations">
      <thead>
        <tr>
          <td />
          <th v-for="col in matrix.cols" :key="col.id" scope="col" role="columnheader">
            {{ col.name }}
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, r) in matrix.rows" :key="row.id" role="row">
          <th scope="row" role="rowheader">{{ row.name }}</th>
          <td
            v-for="(col, c) in matrix.cols"
            :key="col.id"
            role="gridcell"
            class="matrix__cell"
            :class="{ 'matrix__cell--blocked': isBlocked(r, c), 'matrix__cell--open': isSelected(r, c) }"
            :data-r="r"
            :data-c="c"
            :tabindex="focus.row === r && focus.col === c ? 0 : -1"
            :aria-label="label(r, c)"
            :aria-disabled="isBlocked(r, c) ? 'true' : undefined"
            :aria-selected="isSelected(r, c)"
            @click="open(r, c)"
            @keydown="onKeydown($event, r, c)"
          >
            <span v-for="link in linksIn(r, c)" :key="link.id" class="matrix__pill">
              → {{ RELATIONSHIP_LABELS[link.relationship_type] }}
            </span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.matrix-scroll {
  overflow: auto;
}
.matrix {
  border-collapse: collapse;
}
.matrix th,
.matrix td {
  border: 1px solid var(--border, #c9c4d1);
  padding: 0.25rem 0.5rem;
  text-align: left;
}
.matrix__cell {
  min-width: 7rem;
  cursor: pointer;
}
.matrix__cell:focus-visible {
  outline: 2px solid var(--accent, #5b3fd1);
  outline-offset: -2px;
}
.matrix__cell--open {
  background: color-mix(in srgb, var(--accent, #5b3fd1) 15%, transparent);
}
.matrix__cell--blocked {
  background: repeating-linear-gradient(45deg, transparent 0 6px, rgb(128 128 128 / 0.25) 6px 8px);
  cursor: not-allowed;
}
.matrix__pill {
  display: block;
  font-size: 0.85em;
  white-space: nowrap;
}
</style>
