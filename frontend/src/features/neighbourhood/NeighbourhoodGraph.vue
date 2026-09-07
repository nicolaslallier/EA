<script setup lang="ts">
// The drawing itself: boxes on concentric rings, one ring per hop.
//
// It renders a `GraphRead` and emits which element the user wants to look at
// next; it fetches nothing and decides nothing. All the geometry comes from
// `layout.ts`, so what is left here is SVG and interaction — and a change of
// layout never touches this file.
import { computed, onMounted, ref, useTemplateRef, watch } from 'vue'

import { BOX_TEXT, LAYER_COLOURS, LAYER_LABELS } from '../metamodel/useMetamodel'
import { verbOf } from '../relationships/labels'
import { BOX_HEIGHT, BOX_WIDTH, layout, type GraphRead } from './layout'
import type { ElementRead } from './useNeighbourhood'

const props = withDefaults(
  defineProps<{
    graph: GraphRead
    /** The element at the centre. */
    rootId: string
    /** How an element type reads, from the metamodel; raw value by default. */
    typeLabel?: (value: string) => string
  }>(),
  { typeLabel: (value: string) => value },
)

const emit = defineEmits<{ focus: [element: ElementRead] }>()

/** A name longer than a box gets cut; the whole one stays in the tooltip. */
const NAME_LIMIT = 17
const ZOOM_STEP = 0.25
const ZOOM_RANGE = [0.4, 2] as const
/** The smallest scale a drawing shrinks itself to before the user asks. */
const LEGIBLE = 0.75

const canvas = useTemplateRef<HTMLElement>('canvas')
const zoom = ref(1)

const diagram = computed(() => layout(props.graph, props.rootId))

/** The layers actually drawn — a legend of eight when two are shown is noise. */
const legend = computed(() =>
  [...new Set(diagram.value.nodes.map((node) => node.element.layer))].map((layer) => ({
    layer,
    label: LAYER_LABELS[layer],
    colour: LAYER_COLOURS[layer],
  })),
)

const summary = computed(() => {
  const elements = diagram.value.nodes.length
  const links = diagram.value.edges.length
  const hops = diagram.value.hops
  return (
    `${elements} élément${elements > 1 ? 's' : ''}, ` +
    `${links} relation${links > 1 ? 's' : ''}, ` +
    `jusqu'à ${hops} saut${hops > 1 ? 's' : ''}.`
  )
})

/** Recentring is what a click on a neighbour means; the subject is already it. */
function activate(node: { element: ElementRead; hops: number }): void {
  if (node.hops !== 0) {
    emit('focus', node.element)
  }
}

function shorten(name: string): string {
  return name.length > NAME_LIMIT ? `${name.slice(0, NAME_LIMIT - 1)}…` : name
}

function step(by: number): void {
  zoom.value = Math.min(ZOOM_RANGE[1], Math.max(ZOOM_RANGE[0], zoom.value + by))
}

/**
 * Scale the drawing to the width available, when the browser reports one.
 *
 * A layout with no width to measure — jsdom, a hidden tab — keeps whatever
 * scale it had rather than collapsing to zero.
 */
function scaleToWidth(floor: number, ceiling: number): void {
  const width = canvas.value?.clientWidth ?? 0
  if (width > 0) {
    zoom.value = Math.min(ceiling, Math.max(floor, width / diagram.value.size))
  }
}

/** What the button does: show the whole thing, however small that has to be. */
const fit = () => scaleToWidth(ZOOM_RANGE[0], ZOOM_RANGE[1])

// A drawing arrives shrunk just enough to show more of itself, and never past
// the point where a name stops being readable: a deep neighbourhood is several
// times wider than the column it sits in, and the choice between scrolling and
// squinting belongs to the user, not to the first paint.
const settle = () => scaleToWidth(LEGIBLE, 1)

onMounted(settle)
watch(diagram, settle, { flush: 'post' })
</script>

<template>
  <figure class="graph">
    <figcaption class="graph__caption">
      <p class="graph__summary">{{ summary }}</p>
      <div class="graph__zoom" role="group" aria-label="Échelle du schéma">
        <button type="button" aria-label="Dézoomer" @click="step(-ZOOM_STEP)">−</button>
        <button type="button" aria-label="Zoomer" @click="step(ZOOM_STEP)">+</button>
        <button type="button" @click="fit">Ajuster</button>
      </div>
    </figcaption>

    <div ref="canvas" class="graph__canvas">
      <svg
        :viewBox="diagram.viewBox"
        :width="diagram.size * zoom"
        :height="diagram.size * zoom"
        :aria-label="`Voisinage : ${summary}`"
      >
        <defs>
          <marker
            id="ea-arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" class="graph__head" />
          </marker>
        </defs>

        <!-- The rings are the reading key: one circle per hop from the centre. -->
        <circle
          v-for="(radius, index) in diagram.rings"
          :key="index"
          class="graph__ring"
          :cx="diagram.size / 2"
          :cy="diagram.size / 2"
          :r="radius"
        />

        <g v-for="edge in diagram.edges" :key="edge.relationship.id" class="graph__edge">
          <path :d="edge.path" marker-end="url(#ea-arrow)" />
          <text :x="edge.labelX" :y="edge.labelY" text-anchor="middle">
            {{ verbOf(edge.relationship.relationship_type, edge.relationship.access_type) }}
          </text>
        </g>

        <g
          v-for="node in diagram.nodes"
          :key="node.element.id"
          class="graph__node"
          :class="{ 'graph__node--subject': node.hops === 0 }"
          :role="node.hops === 0 ? undefined : 'button'"
          :tabindex="node.hops === 0 ? undefined : 0"
          :aria-label="
            node.hops === 0
              ? `Sujet : ${node.element.name}`
              : `Centrer sur ${node.element.name}`
          "
          @click="activate(node)"
          @keydown.enter.prevent="activate(node)"
          @keydown.space.prevent="activate(node)"
        >
          <title>
            {{ node.element.name }} — {{ typeLabel(node.element.element_type) }}
            ({{ node.hops }} saut{{ node.hops > 1 ? 's' : '' }})
          </title>
          <rect
            :x="node.x - BOX_WIDTH / 2"
            :y="node.y - BOX_HEIGHT / 2"
            :width="BOX_WIDTH"
            :height="BOX_HEIGHT"
            rx="6"
            :fill="LAYER_COLOURS[node.element.layer]"
          />
          <text
            class="graph__name"
            :x="node.x"
            :y="node.y - 2"
            text-anchor="middle"
            :fill="BOX_TEXT"
          >
            {{ shorten(node.element.name) }}
          </text>
          <text
            class="graph__type"
            :x="node.x"
            :y="node.y + 13"
            text-anchor="middle"
            :fill="BOX_TEXT"
          >
            {{ shorten(typeLabel(node.element.element_type)) }}
          </text>
        </g>
      </svg>
    </div>

    <ul class="graph__legend">
      <li v-for="entry in legend" :key="entry.layer">
        <span class="graph__swatch" :style="{ background: entry.colour }" aria-hidden="true" />
        {{ entry.label }}
      </li>
    </ul>
  </figure>
</template>

<style scoped>
.graph {
  margin: 0;
  display: grid;
  gap: 0.6rem;
}
.graph__caption {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.6rem;
}
.graph__summary {
  margin: 0;
  font-size: 0.85rem;
  opacity: 0.75;
}
.graph__zoom {
  display: flex;
  gap: 0.3rem;
}
.graph__zoom button {
  font: inherit;
  padding: 0.2rem 0.6rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.graph__canvas {
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
  /* Tall enough to be a drawing, short enough to leave the controls visible. */
  max-height: 34rem;
}
.graph__ring {
  fill: none;
  stroke: var(--border);
  stroke-dasharray: 3 6;
}
.graph__edge path {
  fill: none;
  stroke: var(--text);
  stroke-width: 1.2;
  opacity: 0.55;
}
.graph__head {
  fill: var(--text);
  opacity: 0.55;
}
.graph__edge text {
  font-size: 10px;
  fill: var(--text);
  opacity: 0.7;
  /* A halo of page colour, so a verb stays readable over a stroke or a box. */
  paint-order: stroke;
  stroke: var(--bg);
  stroke-width: 3px;
}
.graph__node rect {
  stroke: #6f6b78;
  stroke-width: 1;
}
.graph__node--subject rect {
  stroke: var(--text);
  stroke-width: 2.5;
}
.graph__node[role='button'] {
  cursor: pointer;
}
.graph__node[role='button']:hover rect,
.graph__node[role='button']:focus-visible rect {
  stroke: var(--text);
  stroke-width: 2;
}
.graph__name {
  font-size: 12px;
  font-weight: 600;
}
.graph__type {
  font-size: 9.5px;
  opacity: 0.75;
}
.graph__legend {
  display: flex;
  flex-wrap: wrap;
  gap: 0.2rem 1rem;
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 0.8rem;
}
.graph__legend li {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}
.graph__swatch {
  width: 0.8rem;
  height: 0.8rem;
  border: 1px solid #6f6b78;
  border-radius: 3px;
}
</style>
