<script setup lang="ts">
// The roadmap, drawn by hand in SVG over `lib/roadmapLayout.ts`: one row per
// plateau, work package and deliverable, diamonds for the day a plateau is
// reached, bars for the rest, a vertical line for the as-of day. Every mark is
// a real focusable control that opens the element; whatever carries no date is
// listed below rather than dropped.
import { computed } from 'vue'

import { BOX_TEXT, LAYER_COLOURS } from '../features/metamodel/useMetamodel'
import {
  AXIS_HEIGHT,
  ROW_HEIGHT,
  layoutRoadmap,
  type DayWindow,
  type RoadmapItemRead,
  type RoadmapMark,
} from '../lib/roadmapLayout'

const WIDTH = 960
const DIAMOND = 9

const props = defineProps<{ items: RoadmapItemRead[]; window: DayWindow; asOf?: string }>()
const emit = defineEmits<{ open: [id: string] }>()

const drawing = computed(() => layoutRoadmap(props.items, props.window, WIDTH, props.asOf))

function name(mark: RoadmapMark): string {
  const { valid_from: from, valid_until: until } = mark.item
  return `${mark.item.name} — du ${from ?? '…'} au ${until ?? '…'}`
}

function diamond(mark: RoadmapMark): string {
  const cy = mark.y + ROW_HEIGHT / 2
  return [
    [mark.x, cy - DIAMOND],
    [mark.x + DIAMOND, cy],
    [mark.x, cy + DIAMOND],
    [mark.x - DIAMOND, cy],
  ]
    .map(([x, y]) => `${x},${y}`)
    .join(' ')
}
</script>

<template>
  <figure class="roadmap-chart">
    <svg
      :viewBox="`0 0 ${drawing.width} ${drawing.height}`"
      :width="drawing.width"
      :height="drawing.height"
      role="group"
      aria-label="Feuille de route"
    >
      <g class="roadmap-chart__axis">
        <g v-for="tick in drawing.ticks" :key="tick.label">
          <line :x1="tick.x" :x2="tick.x" :y1="AXIS_HEIGHT - 6" :y2="drawing.height" />
          <text :x="tick.x + 3" :y="AXIS_HEIGHT - 10">{{ tick.label }}</text>
        </g>
      </g>

      <line
        v-if="drawing.today !== null"
        data-testid="asof-line"
        class="roadmap-chart__asof"
        :x1="drawing.today"
        :x2="drawing.today"
        y1="0"
        :y2="drawing.height"
      />

      <g
        v-for="mark in drawing.marks"
        :key="mark.item.id"
        class="roadmap-chart__mark"
        role="button"
        tabindex="0"
        :aria-label="name(mark)"
        @click="emit('open', mark.item.id)"
        @keydown.enter.prevent="emit('open', mark.item.id)"
      >
        <title>{{ name(mark) }}</title>
        <text :x="6" :y="mark.y + ROW_HEIGHT / 2 + 4" class="roadmap-chart__label">
          {{ mark.item.name }}
        </text>
        <polygon
          v-if="mark.kind === 'milestone'"
          :points="diamond(mark)"
          :fill="LAYER_COLOURS.implementation_migration"
        />
        <rect
          v-else
          :x="mark.x"
          :y="mark.y + 5"
          :width="mark.width"
          :height="ROW_HEIGHT - 10"
          rx="4"
          :fill="LAYER_COLOURS.implementation_migration"
          :stroke-dasharray="mark.clippedStart || mark.clippedEnd ? '4 2' : undefined"
        />
        <text
          v-if="mark.kind === 'bar' && mark.width > 70"
          :x="mark.x + 6"
          :y="mark.y + ROW_HEIGHT / 2 + 4"
          class="roadmap-chart__inside"
          :fill="BOX_TEXT"
        >
          {{ mark.item.name }}
        </text>
      </g>
    </svg>

    <section
      v-if="drawing.undated.length"
      aria-labelledby="roadmap-undated"
      class="roadmap-chart__undated"
    >
      <h3 id="roadmap-undated">Non daté</h3>
      <ul>
        <li v-for="item in drawing.undated" :key="item.id">
          <button type="button" @click="emit('open', item.id)">{{ item.name }}</button>
        </li>
      </ul>
    </section>
  </figure>
</template>

<style scoped>
.roadmap-chart {
  margin: 0;
  overflow-x: auto;
}
svg {
  max-width: 100%;
  height: auto;
}
.roadmap-chart__axis line {
  stroke: var(--border);
}
.roadmap-chart__axis text,
.roadmap-chart__label {
  font-size: 11px;
  fill: currentColor;
}
.roadmap-chart__asof {
  stroke: currentColor;
  stroke-width: 2;
  stroke-dasharray: 6 3;
}
.roadmap-chart__mark {
  cursor: pointer;
}
.roadmap-chart__mark rect,
.roadmap-chart__mark polygon {
  stroke: var(--border);
}
.roadmap-chart__mark:focus-visible rect,
.roadmap-chart__mark:hover rect,
.roadmap-chart__mark:focus-visible polygon,
.roadmap-chart__mark:hover polygon {
  stroke: currentColor;
  stroke-width: 2;
}
.roadmap-chart__inside {
  font-size: 11px;
  pointer-events: none;
}
.roadmap-chart__undated h3 {
  margin: 1rem 0 0.4rem;
  font-size: 1rem;
}
.roadmap-chart__undated ul {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  list-style: none;
  margin: 0;
  padding: 0;
}
.roadmap-chart__undated button {
  font: inherit;
  color: inherit;
  background: none;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.2rem 0.6rem;
  cursor: pointer;
}
</style>
