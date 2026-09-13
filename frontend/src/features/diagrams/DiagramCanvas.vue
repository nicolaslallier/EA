<script setup lang="ts">
// The drawing a user builds by hand: boxes where they were dropped, the links
// between them, and the three gestures — drop, move, link.
//
// It fetches nothing and decides nothing: it emits what the user did, in
// canvas units, and the section turns that into a layout or a relationship.
// The arithmetic is `lib/diagramGeometry.ts`, so what is left here is SVG and
// pointer bookkeeping.
import { computed, ref, useTemplateRef } from 'vue'

import { BOX_TEXT, LAYER_COLOURS } from '../metamodel/useMetamodel'
import { verbOf } from '../relationships/labels'
import {
  canvasExtent,
  centreOf,
  edgeSegment,
  placeCentredAt,
  toCanvasPoint,
  type Point,
} from '../../lib/diagramGeometry'
import { BOX_HEIGHT, BOX_WIDTH } from '../../lib/graphLayout'
import { ELEMENT_DRAG_TYPE, type Box, type RelationshipRead } from './useDiagram'

const props = defineProps<{
  boxes: Box[]
  relationships: RelationshipRead[]
  /** The element `?element=` names, or ''. */
  selectedId: string
  typeLabel: (value: string) => string
}>()

const emit = defineEmits<{
  place: [elementId: string, at: Point]
  move: [elementId: string, to: Point]
  /** A move gesture ended: the moment to save. */
  moved: []
  select: [elementId: string]
  remove: [elementId: string]
  connect: [sourceId: string, targetId: string]
}>()

const NAME_LIMIT = 17
const ZOOM_STEP = 0.25
const ZOOM_RANGE = [0.4, 2] as const
/** Room kept past the furthest box, so there is always somewhere to drop. */
const MARGIN = 160

const svg = useTemplateRef<SVGSVGElement>('svg')
const zoom = ref(1)

type Gesture =
  | { kind: 'move'; elementId: string; offset: Point; moved: boolean }
  | { kind: 'link'; sourceId: string; to: Point }
const gesture = ref<Gesture | null>(null)

const extent = computed(() => canvasExtent(props.boxes, MARGIN))
const byId = computed(() => new Map(props.boxes.map((box) => [box.element.id, box])))
const selected = computed(() => byId.value.get(props.selectedId))

/** Links whose two ends are drawn; overlapping boxes have no sensible stroke. */
const edges = computed(() =>
  props.relationships.flatMap((relationship) => {
    const source = byId.value.get(relationship.source_id)
    const target = byId.value.get(relationship.target_id)
    const segment = source && target ? edgeSegment(source, target) : null
    return segment ? [{ relationship, ...segment }] : []
  }),
)

const summary = computed(() => {
  const boxes = props.boxes.length
  const links = edges.value.length
  return `${boxes} élément${boxes > 1 ? 's' : ''}, ${links} relation${links > 1 ? 's' : ''}`
})

function shorten(name: string): string {
  return name.length > NAME_LIMIT ? `${name.slice(0, NAME_LIMIT - 1)}…` : name
}

function step(by: number): void {
  zoom.value = Math.min(ZOOM_RANGE[1], Math.max(ZOOM_RANGE[0], zoom.value + by))
}

/** Where the pointer is, in canvas units. */
function at(event: MouseEvent): Point {
  const rect = svg.value?.getBoundingClientRect()
  return toCanvasPoint(
    { x: event.clientX, y: event.clientY },
    { x: rect?.left ?? 0, y: rect?.top ?? 0 },
    zoom.value,
  )
}

/**
 * Keep the pointer's events on the element grabbed, however far it strays.
 *
 * On the element itself rather than the canvas, so a click still lands on the
 * box it started on. jsdom has no pointer capture, hence the optional call.
 */
function capture(event: PointerEvent): void {
  ;(event.currentTarget as Element | null)?.setPointerCapture?.(event.pointerId)
}

function onDrop(event: DragEvent): void {
  const elementId = event.dataTransfer?.getData(ELEMENT_DRAG_TYPE)
  if (elementId) {
    emit('place', elementId, placeCentredAt(at(event)))
  }
}

function grab(event: PointerEvent, box: Box): void {
  if (event.button !== 0) {
    return
  }
  capture(event)
  const pointer = at(event)
  gesture.value = {
    kind: 'move',
    elementId: box.element.id,
    offset: { x: pointer.x - box.x, y: pointer.y - box.y },
    moved: false,
  }
}

function startLink(event: PointerEvent): void {
  if (event.button !== 0 || !selected.value) {
    return
  }
  capture(event)
  gesture.value = { kind: 'link', sourceId: props.selectedId, to: at(event) }
}

function drag(event: PointerEvent): void {
  const current = gesture.value
  if (!current) {
    return
  }
  const pointer = at(event)
  if (current.kind === 'link') {
    current.to = pointer
    return
  }
  current.moved = true
  emit('move', current.elementId, {
    x: Math.max(0, pointer.x - current.offset.x),
    y: Math.max(0, pointer.y - current.offset.y),
  })
}

/**
 * The system took the pointer away mid-gesture — a touch cancelled, a window
 * losing focus. A move already on screen is still reported, so it is saved; a
 * link is never asked for, since nobody chose where it ends. After a normal
 * `pointerup`, `release` has already cleared the gesture and this does nothing.
 */
function interrupt(): void {
  const current = gesture.value
  gesture.value = null
  if (current?.kind === 'move' && current.moved) {
    emit('moved')
  }
}

function release(event: PointerEvent): void {
  const current = gesture.value
  gesture.value = null
  if (current?.kind === 'move') {
    if (current.moved) {
      emit('moved')
    }
    return
  }
  if (current?.kind === 'link') {
    const pointer = at(event)
    // The topmost box under the pointer: the last one drawn.
    const target = [...props.boxes].reverse().find(
      (box) =>
        pointer.x >= box.x &&
        pointer.x <= box.x + BOX_WIDTH &&
        pointer.y >= box.y &&
        pointer.y <= box.y + BOX_HEIGHT,
    )
    if (target && target.element.id !== current.sourceId) {
      emit('connect', current.sourceId, target.element.id)
    }
  }
}

function removeIfSelected(box: Box): void {
  if (box.element.id === props.selectedId) {
    emit('remove', box.element.id)
  }
}
</script>

<template>
  <figure class="canvas">
    <figcaption class="canvas__caption">
      <p class="canvas__summary">{{ summary }}</p>
      <div class="canvas__zoom" role="group" aria-label="Échelle du diagramme">
        <button type="button" aria-label="Dézoomer" @click="step(-ZOOM_STEP)">−</button>
        <button type="button" aria-label="Zoomer" @click="step(ZOOM_STEP)">+</button>
      </div>
    </figcaption>

    <p v-if="boxes.length === 0" class="canvas__hint">
      Glisse un élément de la palette jusqu'ici.
    </p>

    <div class="canvas__area">
      <svg
        ref="svg"
        role="group"
        :aria-label="`Zone de dessin : ${summary}`"
        :viewBox="`0 0 ${extent.width} ${extent.height}`"
        :width="extent.width * zoom"
        :height="extent.height * zoom"
        @dragover.prevent
        @drop.prevent="onDrop"
        @pointermove="drag"
        @pointerup="release"
        @pointercancel="interrupt"
        @lostpointercapture="interrupt"
      >
        <defs>
          <marker
            id="ea-diagram-arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" class="canvas__head" />
          </marker>
        </defs>

        <g v-for="edge in edges" :key="edge.relationship.id" class="canvas__edge">
          <line
            :x1="edge.from.x"
            :y1="edge.from.y"
            :x2="edge.to.x"
            :y2="edge.to.y"
            marker-end="url(#ea-diagram-arrow)"
          />
          <text
            :x="(edge.from.x + edge.to.x) / 2"
            :y="(edge.from.y + edge.to.y) / 2 - 4"
            text-anchor="middle"
          >
            {{ verbOf(edge.relationship.relationship_type, edge.relationship.access_type) }}
          </text>
        </g>

        <g
          v-for="box in boxes"
          :key="box.element.id"
          class="canvas__node"
          :class="{ 'canvas__node--selected': box.element.id === selectedId }"
          role="button"
          tabindex="0"
          :aria-pressed="box.element.id === selectedId"
          :aria-label="`${box.element.name} — ${typeLabel(box.element.element_type)}`"
          @pointerdown="grab($event, box)"
          @click="emit('select', box.element.id)"
          @keydown.enter.prevent="emit('select', box.element.id)"
          @keydown.delete.prevent="removeIfSelected(box)"
        >
          <title>{{ box.element.name }} — {{ typeLabel(box.element.element_type) }}</title>
          <rect
            :x="box.x"
            :y="box.y"
            :width="BOX_WIDTH"
            :height="BOX_HEIGHT"
            rx="6"
            :fill="LAYER_COLOURS[box.element.layer]"
          />
          <text
            class="canvas__name"
            :x="box.x + BOX_WIDTH / 2"
            :y="box.y + BOX_HEIGHT / 2 - 2"
            text-anchor="middle"
            :fill="BOX_TEXT"
          >
            {{ shorten(box.element.name) }}
          </text>
          <text
            class="canvas__type"
            :x="box.x + BOX_WIDTH / 2"
            :y="box.y + BOX_HEIGHT / 2 + 13"
            text-anchor="middle"
            :fill="BOX_TEXT"
          >
            {{ shorten(typeLabel(box.element.element_type)) }}
          </text>
        </g>

        <line
          v-if="gesture?.kind === 'link' && selected"
          class="canvas__band"
          :x1="centreOf(selected).x"
          :y1="centreOf(selected).y"
          :x2="gesture.to.x"
          :y2="gesture.to.y"
        />

        <circle
          v-if="selected"
          class="canvas__handle"
          :cx="selected.x + BOX_WIDTH"
          :cy="selected.y + BOX_HEIGHT / 2"
          r="7"
          :aria-label="`Relier « ${selected.element.name} » à un autre élément`"
          @pointerdown.stop="startLink"
        >
          <title>Glisse jusqu'à un autre élément pour les relier</title>
        </circle>
      </svg>
    </div>
  </figure>
</template>

<style scoped>
.canvas {
  margin: 0;
  display: grid;
  gap: 0.5rem;
}
.canvas__caption {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.6rem;
}
.canvas__summary,
.canvas__hint {
  margin: 0;
  font-size: 0.85rem;
  opacity: 0.75;
}
.canvas__zoom {
  display: flex;
  gap: 0.3rem;
}
.canvas__zoom button {
  font: inherit;
  padding: 0.2rem 0.6rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.canvas__area {
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
  max-height: 40rem;
}
svg {
  display: block;
  /* A drag on the canvas moves a box, never the page. */
  touch-action: none;
}
.canvas__edge line {
  stroke: var(--text);
  stroke-width: 1.2;
  opacity: 0.55;
}
.canvas__head {
  fill: var(--text);
  opacity: 0.55;
}
.canvas__edge text {
  font-size: 10px;
  fill: var(--text);
  opacity: 0.7;
  paint-order: stroke;
  stroke: var(--bg);
  stroke-width: 3px;
}
.canvas__node {
  cursor: grab;
}
.canvas__node rect {
  stroke: #6f6b78;
  stroke-width: 1;
}
.canvas__node:focus-visible rect,
.canvas__node:hover rect {
  stroke: var(--text);
  stroke-width: 2;
}
.canvas__node--selected rect {
  stroke: var(--text);
  stroke-width: 2.5;
}
.canvas__node text {
  pointer-events: none;
  user-select: none;
}
.canvas__name {
  font-size: 12px;
  font-weight: 600;
}
.canvas__type {
  font-size: 9.5px;
  opacity: 0.75;
}
.canvas__handle {
  fill: var(--bg);
  stroke: var(--text);
  stroke-width: 2;
  cursor: crosshair;
}
.canvas__band {
  stroke: var(--text);
  stroke-width: 1.5;
  stroke-dasharray: 5 4;
  pointer-events: none;
}
</style>
