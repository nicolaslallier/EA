<script setup lang="ts">
// The classification tree, drawn as a WAI-ARIA tree.
//
// It is flat in the DOM — one `<li role="treeitem">` per visible node, the
// depth in `aria-level` — which keeps the keyboard handling to a list index,
// and it knows nothing of the API: it draws the packages it is given, says
// what was selected, and says what was dropped on what. The screen decides.
//
// Two nodes come before the packages: "all" (no filter) and "the root" (the
// unfiled). A package id that names nothing selects nothing — a link to a
// deleted package must not break the screen.
import { computed, nextTick, ref, watch } from 'vue'

import {
  ALL,
  ELEMENT_DRAG_TYPE,
  ROOT,
  ancestorsOf,
  buildTree,
  visibleNodes,
  type PackageRead,
  type TreeNode,
} from './packageTree'

const props = defineProps<{ packages: PackageRead[]; selected: string; canDrop: boolean }>()
const emit = defineEmits<{ select: [value: string]; drop: [elementId: string, target: string] }>()

type Item = {
  key: string
  value: string
  label: string
  depth: number
  count: number | null
  node: TreeNode | null
}

const ALL_KEY = '__all__'

const expanded = ref(new Set<string>())
const focusKey = ref(ALL_KEY)
const dropTarget = ref('')
const treeElement = ref<HTMLElement | null>(null)

const items = computed<Item[]>(() => [
  { key: ALL_KEY, value: ALL, label: 'Tous les éléments', depth: 0, count: null, node: null },
  { key: ROOT, value: ROOT, label: 'Racine (sans paquet)', depth: 0, count: null, node: null },
  ...visibleNodes(buildTree(props.packages), expanded.value).map((node) => ({
    key: node.package.id,
    value: node.package.id,
    label: node.package.name,
    depth: node.depth,
    count: node.package.element_count,
    node,
  })),
])

// The path to the selected package is opened, so a shared link lands on a
// tree in which the selection can be seen.
watch(
  () => [props.selected, props.packages] as const,
  ([selected, packages]) => {
    const opened = ancestorsOf(selected, packages)
    if (opened.length > 0) {
      expanded.value = new Set([...expanded.value, ...opened])
    }
    focusKey.value = packages.some((entry) => entry.id === selected)
      ? selected
      : selected === ROOT
        ? ROOT
        : ALL_KEY
  },
  { immediate: true },
)

function hasChildren(item: Item): boolean {
  return (item.node?.children.length ?? 0) > 0
}

function toggle(key: string, open: boolean): void {
  const next = new Set(expanded.value)
  if (open) {
    next.add(key)
  } else {
    next.delete(key)
  }
  expanded.value = next
}

async function focus(key: string): Promise<void> {
  focusKey.value = key
  await nextTick()
  treeElement.value?.querySelector<HTMLElement>(`[data-key="${key}"]`)?.focus()
}

function onKeydown(event: KeyboardEvent): void {
  const current = (event.target as HTMLElement).closest<HTMLElement>('[role="treeitem"]')
  const list = items.value
  const index = list.findIndex((candidate) => candidate.key === current?.dataset.key)
  const item = list[index]
  if (!item) {
    return
  }
  const go = (to: number): void => {
    event.preventDefault()
    const target = list[Math.max(0, Math.min(to, list.length - 1))]
    if (target) {
      void focus(target.key)
    }
  }
  switch (event.key) {
    case 'ArrowDown':
      go(index + 1)
      break
    case 'ArrowUp':
      go(index - 1)
      break
    case 'Home':
      go(0)
      break
    case 'End':
      go(list.length - 1)
      break
    case 'ArrowRight':
      if (item.node && hasChildren(item)) {
        event.preventDefault()
        if (expanded.value.has(item.key)) {
          void focus(item.node.children[0]?.package.id ?? item.key)
        } else {
          toggle(item.key, true)
        }
      }
      break
    case 'ArrowLeft':
      if (item.node) {
        event.preventDefault()
        const parent = item.node.package.parent_id
        if (hasChildren(item) && expanded.value.has(item.key)) {
          toggle(item.key, false)
        } else if (parent && list.some((candidate) => candidate.key === parent)) {
          void focus(parent)
        }
      }
      break
    case 'Enter':
    case ' ':
      event.preventDefault()
      emit('select', item.value)
      break
  }
}

function acceptsDrops(item: Item): boolean {
  return props.canDrop && item.value !== ALL
}

function onDragOver(event: DragEvent, item: Item): void {
  if (!acceptsDrops(item) || !event.dataTransfer?.types.includes(ELEMENT_DRAG_TYPE)) {
    return
  }
  event.preventDefault()
  dropTarget.value = item.key
}

function onDrop(event: DragEvent, item: Item): void {
  dropTarget.value = ''
  if (!acceptsDrops(item)) {
    return
  }
  const elementId = event.dataTransfer?.getData(ELEMENT_DRAG_TYPE)
  if (elementId) {
    event.preventDefault()
    emit('drop', elementId, item.value)
  }
}
</script>

<template>
  <ul ref="treeElement" class="tree" role="tree" aria-label="Paquets" @keydown="onKeydown">
    <li
      v-for="item in items"
      :key="item.key"
      role="treeitem"
      class="node"
      :class="{
        'node--selected': selected === item.value,
        'node--drop': dropTarget === item.key,
      }"
      :data-key="item.key"
      :aria-level="item.depth + 1"
      :aria-selected="selected === item.value"
      :aria-expanded="hasChildren(item) ? expanded.has(item.key) : undefined"
      :tabindex="focusKey === item.key ? 0 : -1"
      :style="{ paddingLeft: `${0.4 + item.depth * 1.1}rem` }"
      @click="emit('select', item.value)"
      @focus="focusKey = item.key"
      @dragover="onDragOver($event, item)"
      @dragleave="dropTarget = ''"
      @drop="onDrop($event, item)"
    >
      <!-- The keyboard has the arrows; this is the mouse's way to the same place. -->
      <span
        v-if="hasChildren(item)"
        class="twisty"
        aria-hidden="true"
        @click.stop="toggle(item.key, !expanded.has(item.key))"
      >
        {{ expanded.has(item.key) ? '▾' : '▸' }}
      </span>
      <span v-else class="twisty" aria-hidden="true" />
      <span class="label">{{ item.label }}</span>
      <span v-if="item.count !== null" class="count">{{ item.count }}</span>
    </li>
  </ul>
</template>

<style scoped>
.tree {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 1px;
}
.node {
  display: flex;
  align-items: baseline;
  gap: 0.3rem;
  padding-block: 0.25rem;
  padding-right: 0.4rem;
  border-radius: 6px;
  cursor: pointer;
}
.node:hover {
  background: color-mix(in srgb, currentColor 8%, transparent);
}
.node--selected {
  font-weight: 600;
  background: color-mix(in srgb, currentColor 14%, transparent);
}
.node--drop {
  outline: 2px dashed currentColor;
}
.node:focus-visible {
  outline: 2px solid currentColor;
}
.twisty {
  display: inline-block;
  width: 1rem;
  flex: none;
  text-align: center;
}
.label {
  flex: 1;
  overflow-wrap: anywhere;
}
.count {
  opacity: 0.65;
  font-size: 0.85em;
}
</style>
