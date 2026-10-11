<script setup lang="ts">
// The heat map: rectangles, a printed value in each, a tooltip, a legend.
//
// Hand-drawn SVG over `lib/heatmapLayout.ts`. A cell is an `<a>` when it
// stands for something the catalogue can filter on, and its value is printed
// in the cell on top of the tint, so the picture does not depend on colour
// alone. Colours come from the server's `level`, mapped by `presentation.ts`.
import { computed } from 'vue'
import { useRouter } from 'vue-router'

import { layoutHeatmap, legendLevels } from '../../lib/heatmapLayout'
import { CELL_HEIGHT, CELL_WIDTH, HEADER_HEIGHT, HEADER_WIDTH } from '../../lib/heatmapLayout'
import { catalogueQuery, keyLabel, swatchOf } from './presentation'
import type { HeatmapRead } from './useDashboard'

const props = defineProps<{ heatmap: HeatmapRead }>()
const router = useRouter()

const layout = computed(() => layoutHeatmap(props.heatmap.rows, props.heatmap.cols, props.heatmap.cells))
const levels = computed(() => legendLevels(props.heatmap.cells))

function shown(value: number | null, count: number): string {
  if (value === null) {
    return count === 0 ? '' : '—'
  }
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

function tooltip(row: string, col: string, count: number, value: number | null): string {
  const base = `${keyLabel(row)} × ${keyLabel(col)} : ${count} élément${count > 1 ? 's' : ''}`
  return props.heatmap.metric === 'count' || value === null
    ? base
    : `${base} — ${props.heatmap.metric} = ${shown(value, count)}`
}

function hrefOf(row: string, col: string): string | null {
  const query = catalogueQuery(props.heatmap.rows_by, row, props.heatmap.cols_by, col)
  return query ? router.resolve({ path: '/elements', query }).href : null
}

function go(event: MouseEvent, href: string): void {
  event.preventDefault()
  void router.push(href)
}
</script>

<template>
  <figure class="heatmap">
    <svg
      role="img"
      aria-label="Carte de chaleur"
      :viewBox="layout.viewBox"
      :width="layout.width"
      :height="layout.height"
    >
      <text
        v-for="header in layout.colHeaders"
        :key="`c-${header.key}`"
        :x="header.x + CELL_WIDTH / 2"
        :y="HEADER_HEIGHT - 4"
        text-anchor="middle"
        class="heatmap__header"
      >
        {{ keyLabel(header.key) }}
      </text>
      <text
        v-for="header in layout.rowHeaders"
        :key="`r-${header.key}`"
        :x="HEADER_WIDTH - 4"
        :y="header.y + CELL_HEIGHT / 2 + 4"
        text-anchor="end"
        class="heatmap__header"
      >
        {{ keyLabel(header.key) }}
      </text>
      <template v-for="rect in layout.rects" :key="`${rect.row}/${rect.col}`">
        <component
          :is="hrefOf(rect.row, rect.col) ? 'a' : 'g'"
          :href="hrefOf(rect.row, rect.col) ?? undefined"
          @click="hrefOf(rect.row, rect.col) && go($event, hrefOf(rect.row, rect.col) as string)"
        >
          <title>{{ tooltip(rect.row, rect.col, rect.count, rect.value) }}</title>
          <rect
            :x="rect.x"
            :y="rect.y"
            :width="rect.width"
            :height="rect.height"
            :fill="swatchOf(rect.level).fill"
            rx="3"
          />
          <text
            :x="rect.x + rect.width / 2"
            :y="rect.y + rect.height / 2 + 5"
            text-anchor="middle"
            :fill="swatchOf(rect.level).ink"
            class="heatmap__value"
          >
            {{ shown(rect.value, rect.count) }}
          </text>
        </component>
      </template>
    </svg>
    <figcaption>
      <ul class="heatmap__legend" aria-label="Légende des niveaux">
        <li v-for="level in levels" :key="level">
          <span class="heatmap__swatch" :style="{ background: swatchOf(level).fill }" />
          niveau {{ level + 1 }} sur 5
        </li>
        <li>
          <span class="heatmap__swatch" :style="{ background: swatchOf(null).fill }" />
          pas de donnée
        </li>
      </ul>
    </figcaption>
  </figure>
</template>

<style scoped>
.heatmap__header {
  font-size: 12px;
  fill: var(--text, #24202b);
}
.heatmap__value {
  font-size: 14px;
  font-weight: 600;
}
.heatmap__legend {
  display: flex;
  gap: 1rem;
  list-style: none;
  padding: 0;
}
.heatmap__swatch {
  display: inline-block;
  width: 1rem;
  height: 1rem;
  margin-right: 0.25rem;
  border: 1px solid #8884;
  vertical-align: middle;
}
</style>
