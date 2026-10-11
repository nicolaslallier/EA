<script setup lang="ts">
// The `/analyse/matrice` section: two axes, a grid, and the cell being worked on.
//
// The question lives in the URL (docs/adr/0010): `?rows=` and `?cols=` say what
// each axis is, `?cell=` which pair is open. Changing an axis is a step of an
// investigation and is pushed; opening a cell only replaces, or a dozen cells
// would bury the previous screen in the history.
//
// A reader sees the grid and the links; the write controls are hidden for them
// but it is the API that refuses (docs/adr/0032).
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { messageOf } from '../../lib/api'
import { useMe } from '../../lib/me'
import { LAYER_LABELS, useMetamodel } from '../metamodel/useMetamodel'
import { axisToParam, parseAxis } from './axis'
import { cellToParam, parseCell } from './gridNavigation'
import MatrixCellPanel from './MatrixCellPanel.vue'
import MatrixGrid from './MatrixGrid.vue'
import { useMatrix, type RelationshipType } from './useMatrix'

const route = useRoute()
const router = useRouter()
const { canWrite } = useMe()
const metamodel = useMetamodel()
const matrix = useMatrix()
const failure = ref('')
const busy = ref(false)

function one(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

const rowsParam = computed(() => one(route.query.rows))
const colsParam = computed(() => one(route.query.cols))
const opened = computed(() => parseCell(one(route.query.cell)))

const rowsValue = computed(() => axisToParam(parseAxis(rowsParam.value)))
const colsValue = computed(() => axisToParam(parseAxis(colsParam.value)))

const axes = computed(() => [
  { id: 'rows' as const, label: 'Lignes', value: rowsValue.value, param: rowsParam.value },
  { id: 'cols' as const, label: 'Colonnes', value: colsValue.value, param: colsParam.value },
])

onMounted(async () => {
  await metamodel.load()
})

watch(
  [rowsParam, colsParam],
  async () => {
    await matrix.load(parseAxis(rowsParam.value), parseAxis(colsParam.value))
  },
  { immediate: true },
)

watch(
  opened,
  async (cell) => {
    failure.value = ''
    if (!cell) {
      matrix.closeCell()
      return
    }
    await matrix.openCell(cell.source, cell.target)
  },
  { immediate: true },
)

const names = computed(() => {
  const found = new Map<string, string>()
  for (const element of [...(matrix.matrix.value?.rows ?? []), ...(matrix.matrix.value?.cols ?? [])]) {
    found.set(element.id, element.name)
  }
  return found
})

function nameOf(id: string): string {
  return names.value.get(id) ?? 'élément inconnu'
}

/** Count of what a cut axis shows, for the banner. */
const cut = computed(() => {
  const grid = matrix.matrix.value
  if (!grid?.truncated) {
    return ''
  }
  const parts: string[] = []
  if (grid.rows_total > grid.rows.length) {
    parts.push(`lignes : ${grid.rows.length} premiers sur ${grid.rows_total}`)
  }
  if (grid.cols_total > grid.cols.length) {
    parts.push(`colonnes : ${grid.cols.length} premières sur ${grid.cols_total}`)
  }
  return `${parts.join(' ; ')} — affinez le choix de l'axe.`
})

function chooseAxis(which: 'rows' | 'cols', value: string): void {
  const { cell: _closed, ...rest } = route.query
  void router.push({ query: { ...rest, [which]: value } })
}

function openCell(source: string, target: string): void {
  void router.replace({ query: { ...route.query, cell: cellToParam(source, target) } })
}

function closeCell(): void {
  const { cell: _closed, ...rest } = route.query
  void router.replace({ query: rest })
}

async function act(task: () => Promise<void>): Promise<void> {
  failure.value = ''
  busy.value = true
  try {
    await task()
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

const connect = (type: RelationshipType) => act(() => matrix.connect(type))
const disconnect = (id: string) => act(() => matrix.disconnect(id))
</script>

<template>
  <section class="section">
    <header>
      <h2>Matrice de relations</h2>
      <p class="hint">Qui est relié à quoi : les sources en lignes, les cibles en colonnes.</p>
    </header>

    <form class="controls" aria-label="Axes de la matrice" @submit.prevent>
      <div v-for="axis in axes" :key="axis.id" class="field">
        <label :for="`matrix-${axis.id}`">{{ axis.label }}</label>
        <select
          :id="`matrix-${axis.id}`"
          :value="axis.value"
          @change="chooseAxis(axis.id, ($event.target as HTMLSelectElement).value)"
        >
          <option value="all">Tout le catalogue</option>
          <option v-if="axis.param.startsWith('ids:')" :value="axis.param">
            Une sélection d'éléments
          </option>
          <optgroup label="Une couche">
            <option v-for="layer in metamodel.layers.value" :key="layer" :value="`layer:${layer}`">
              {{ LAYER_LABELS[layer] }}
            </option>
          </optgroup>
          <optgroup v-for="group in metamodel.byLayer.value" :key="group.layer" :label="`Un type — ${LAYER_LABELS[group.layer]}`">
            <option v-for="type in group.types" :key="type.value" :value="`type:${type.value}`">
              {{ type.label }}
            </option>
          </optgroup>
        </select>
      </div>
    </form>

    <p v-if="matrix.status.value === 'error'" class="banner banner--error" role="alert">
      {{ matrix.error.value }}
    </p>
    <p v-else-if="matrix.status.value === 'loading' && !matrix.matrix.value" class="hint">
      Chargement de la matrice…
    </p>

    <template v-if="matrix.matrix.value">
      <p v-if="cut" class="banner" role="status">{{ cut }}</p>
      <MatrixGrid
        :matrix="matrix.matrix.value"
        :selected="opened"
        @open="openCell"
        @close="closeCell"
      />
    </template>

    <MatrixCellPanel
      v-if="matrix.detail.value"
      :detail="matrix.detail.value"
      :source-name="nameOf(matrix.detail.value.source_id)"
      :target-name="nameOf(matrix.detail.value.target_id)"
      :can-write="canWrite"
      :busy="busy"
      :failure="failure || matrix.cellError.value"
      @connect="connect"
      @disconnect="disconnect"
      @close="closeCell"
    />
  </section>
</template>
