<script setup lang="ts">
// The `/diagrammes` section: the saved diagrams, and the editor of one.
//
// Which diagram is open and which element is selected live in the URL
// (`?diagram=`, `?element=`), so an editor can be linked to and the back button
// leaves a diagram. Opening a diagram pushes a history entry; selecting an
// element only replaces one, or every click would bury the list under it.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { messageOf } from '../../lib/api'
import { useMe } from '../../lib/me'
import { download, exportSize, rasterize, serializeSvg, type ExportFormat } from '../../lib/diagramExport'
import type { AlignMode, DistributeAxis, Point } from '../../lib/diagramGeometry'
import { BOX_HEIGHT } from '../../lib/graphLayout'
import ElementDetail from '../elements/ElementDetail.vue'
import { useElementDetail } from '../elements/useElementDetail'
import { useMetamodel } from '../metamodel/useMetamodel'
import PackageTree from '../packages/PackageTree.vue'
import { ALL, ROOT } from '../packages/packageTree'
import { usePackages } from '../packages/usePackages'
import { useAsOf } from '../../lib/asof'
import { verbOf } from '../relationships/labels'
import AnnotationPalette from './AnnotationPalette.vue'
import DiagramCanvas from './DiagramCanvas.vue'
import ObjectInspector from './ObjectInspector.vue'
import DiagramToolbar from './DiagramToolbar.vue'
import { buildExportScene } from './exportScene'
import { useSelection } from './useSelection'
import ElementPalette from './ElementPalette.vue'
import LinkPopover from './LinkPopover.vue'
import { useDiagram, useElementPalette, type ElementRead } from './useDiagram'
import { useDiagrams, type DiagramSummaryRead } from './useDiagrams'

const route = useRoute()
const { asOf } = useAsOf()
const router = useRouter()
const list = useDiagrams()
const editor = useDiagram()
const palette = useElementPalette()
const metamodel = useMetamodel()
const detail = useElementDetail()
const selection = useSelection()
const exportError = ref('')
const selectedObjectId = ref('')
const selectedEdgeId = ref('')
const selectedObject = computed(() => editor.objects.value.find((item) => item.id === selectedObjectId.value))
const selectedEdge = computed(() => {
  const link = editor.relationships.value.find((candidate) => candidate.id === selectedEdgeId.value)
  if (!link) {
    return undefined
  }
  return {
    relationshipId: link.id,
    label: verbOf(link.relationship_type, link.access_type),
    bends: editor.edges.value.find((edge) => edge.relationship_id === link.id)?.waypoints.length ?? 0,
  }
})
const childrenOfSelectedGroup = computed(() =>
  editor.nodes.value
    .filter((node) => node.parent_object_id === selectedObjectId.value)
    .flatMap((node) => {
      const element = editor.byId.value.get(node.element_id)
      return element ? [{ id: element.id, name: element.name }] : []
    }),
)
const anchorChoices = computed(() => editor.boxes.value.map((box) => ({ id: box.element.id, name: box.element.name })))
const packages = usePackages()
// The API refuses a reader's write anyway (docs/adr/0032); this only spares
// them controls that could not work — every one below changes a stored diagram.
const { canWrite } = useMe()

/** A query parameter is `string | string[] | null`; only one value means anything here. */
function one(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

const diagramId = computed(() => one(route.query.diagram))
const selectedId = computed(() => one(route.query.element))
const packageParam = computed(() => one(route.query.package))
const withSubpackages = computed(() => route.query.subpackages !== '0')
/** The package chosen for a new diagram. */
const newPackage = ref('')

// Applied here, synchronously, so the first load — started by the `watch` below
// before any hook runs — already carries the filter.
function applyFilters(): void {
  list.filters.package = packageParam.value
  list.filters.includeSubpackages = withSubpackages.value
}
applyFilters()

async function selectPackage(value: string): Promise<void> {
  const { package: _previous, ...rest } = route.query
  await router.push({ query: value === ALL ? rest : { ...rest, package: value } })
}

async function moveDiagram(diagram: DiagramSummaryRead, value: string): Promise<void> {
  listFailure.value = ''
  try {
    await list.move(diagram.id, value === ROOT ? null : value)
  } catch (caught) {
    listFailure.value = messageOf(caught)
  }
}

function pathOf(packageId: string | null): string {
  return packageId ? (packages.byId.value.get(packageId)?.path ?? '') : ''
}

const newName = ref('')
const listFailure = ref('')
const busy = ref(false)
const confirming = ref<DiagramSummaryRead | null>(null)
/** The pair a link was drawn between, while its form is open. */
const link = ref<{ source: ElementRead; target: ElementRead } | null>(null)

const saveState = computed(() => {
  if (editor.saving.value) {
    return 'Enregistrement…'
  }
  return editor.saveError.value ? '' : 'Enregistré automatiquement.'
})

onMounted(async () => {
  window.addEventListener('keydown', onShortcut)
  await Promise.all([metamodel.load(), packages.load()])
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onShortcut)
})

/** Ctrl/Cmd+Z undoes, +Shift or Y redoes — never while typing in a field, never for a reader. */
function onShortcut(event: KeyboardEvent): void {
  if (!canWrite.value || !(event.ctrlKey || event.metaKey)) {
    return
  }
  const target = event.target as HTMLElement | null
  if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) {
    return
  }
  const key = event.key.toLowerCase()
  if (key === 'z' && event.shiftKey) {
    event.preventDefault()
    editor.redo()
  } else if (key === 'z') {
    event.preventDefault()
    editor.undo()
  } else if (key === 'y') {
    event.preventDefault()
    editor.redo()
  }
}

watch([packageParam, withSubpackages], async () => {
  applyFilters()
  if (!diagramId.value) {
    await list.load()
  }
})

watch(
  [diagramId, asOf],
  async ([id]) => {
    link.value = null
    selection.clear()
    selectedObjectId.value = ''
    selectedEdgeId.value = ''
    if (!id) {
      editor.close()
      await list.load()
      return
    }
    await Promise.all([
      editor.open(id, asOf.value),
      palette.status.value === 'idle' ? palette.search('') : undefined,
    ])
  },
  { immediate: true },
)

watch(
  selectedId,
  async (id) => {
    if (id) {
      await detail.open(id)
    } else {
      detail.close()
    }
  },
  { immediate: true },
)

function select(elementId: string): void {
  void router.replace({ query: { ...route.query, element: elementId } })
}

function deselect(): void {
  const { element: _dropped, ...rest } = route.query
  void router.replace({ query: rest })
}

async function create(): Promise<void> {
  const name = newName.value.trim()
  if (!name) {
    return
  }
  busy.value = true
  listFailure.value = ''
  try {
    const created = await list.create(name, newPackage.value || undefined)
    newName.value = ''
    newPackage.value = ''
    await router.push({ query: { diagram: created.id } })
  } catch (caught) {
    listFailure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

async function confirmDelete(): Promise<void> {
  const doomed = confirming.value
  if (!doomed) {
    return
  }
  busy.value = true
  listFailure.value = ''
  try {
    await list.remove(doomed.id)
    confirming.value = null
  } catch (caught) {
    listFailure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

function onPlace(elementId: string, at: Point): void {
  const element = palette.find(elementId)
  if (element) {
    editor.place(element, at)
  }
}

// ponytail: stacks keyboard-added boxes down the left edge; they may overlap
// boxes already there — the user moves them, as after any drop.
function onAdd(element: ElementRead): void {
  editor.place(element, { x: 24, y: 24 + editor.boxes.value.length * (BOX_HEIGHT + 16) })
}

function removeBox(elementId: string): void {
  editor.remove(elementId)
  if (elementId === selectedId.value) {
    deselect()
  }
}

function onConnect(sourceId: string, targetId: string): void {
  const source = editor.byId.value.get(sourceId)
  const target = editor.byId.value.get(targetId)
  if (source && target) {
    link.value = { source, target }
  }
}
function onSelect(elementId: string): void {
  selection.set([elementId])
  select(elementId)
}

function onSelectMany(ids: string[]): void {
  selection.set(ids)
  if (ids.length === 1) {
    select(ids[0])
  } else {
    deselect()
  }
}

function removeSelected(ids: string[]): void {
  editor.removeMany(ids)
  selection.clear()
  deselect()
}

function arrange(mode: AlignMode): void {
  editor.align(selection.ids.value, mode)
}

function spread(axis: DistributeAxis): void {
  editor.distribute(selection.ids.value, axis)
}

function addAnnotation(kind: 'note' | 'group'): void {
  const offset = editor.objects.value.length * 24
  selectedObjectId.value = editor.addObject(kind, { x: 40 + offset, y: 40 + offset })
  selectedEdgeId.value = ''
}

function selectObject(id: string): void {
  selectedObjectId.value = id
  selectedEdgeId.value = ''
}

function selectEdge(id: string): void {
  selectedEdgeId.value = id
  selectedObjectId.value = ''
}

function removeSelectedObject(): void {
  editor.removeObject(selectedObjectId.value)
  selectedObjectId.value = ''
}

function onLinkContent(sourceId: string, targetId: string): void {
  onConnect(sourceId, targetId)
}

function reload(): void {
  void editor.reloadAfterConflict()
}

async function exportAs(format: ExportFormat): Promise<void> {
  exportError.value = ''
  const scene = buildExportScene(
    editor.boxes.value,
    editor.relationships.value,
    metamodel.labelOf,
    editor.edges.value,
    editor.objects.value,
  )
  const svg = serializeSvg(scene)
  const name = (editor.diagram.value?.name ?? 'diagramme').replace(/[^\p{L}\p{N}_-]+/gu, '-')
  try {
    if (format === 'svg') {
      download(new Blob([svg], { type: 'image/svg+xml;charset=utf-8' }), `${name}.svg`)
    } else {
      download(await rasterize(svg, exportSize(scene), format === 'png2' ? 2 : 1), `${name}.png`)
    }
  } catch (caught) {
    exportError.value = messageOf(caught)
  }
}
</script>

<template>
  <div v-if="!diagramId" class="browser">
    <aside class="browser__tree" aria-label="Rangement">
      <PackageTree
        :packages="packages.packages.value"
        :selected="packageParam"
        :can-drop="false"
        @select="selectPackage"
      />
    </aside>

    <section class="diagrams">
      <header>
        <h2>Diagrammes</h2>
        <p class="hint">Des vues enregistrées : quels éléments, et où. Les faits restent dans le modèle.</p>
      </header>

      <form v-if="canWrite" class="create" aria-label="Créer un diagramme" @submit.prevent="create">
        <div class="field">
          <label for="diagram-name">Nom du diagramme</label>
          <input id="diagram-name" v-model="newName" maxlength="200" autocomplete="off" />
        </div>
        <div class="field">
          <label for="diagram-package">Paquet</label>
          <select id="diagram-package" v-model="newPackage">
            <option value="">Racine (sans paquet)</option>
            <option v-for="entry in packages.packages.value" :key="entry.id" :value="entry.id">
              {{ entry.path }}
            </option>
          </select>
        </div>
        <button type="submit" :disabled="busy || !newName.trim()">Créer</button>
      </form>

      <p v-if="listFailure" class="banner banner--error" role="alert">{{ listFailure }}</p>
      <p v-if="list.status.value === 'error'" class="banner banner--error" role="alert">
        {{ list.error.value }}
      </p>

      <p v-if="confirming" class="banner banner--confirm" role="alert">
        Supprimer le diagramme « {{ confirming.name }} » ? Les éléments sont conservés.
        <button type="button" :disabled="busy" @click="confirmDelete">Confirmer</button>
        <button type="button" class="secondary" @click="confirming = null">Renoncer</button>
      </p>

      <p v-if="list.status.value === 'ready' && list.diagrams.value.length === 0" class="hint">
        Aucun diagramme pour l'instant.
      </p>

      <ul class="list">
        <li v-for="diagram in list.diagrams.value" :key="diagram.id">
          <RouterLink :to="{ query: { diagram: diagram.id } }">{{ diagram.name }}</RouterLink>
          <span class="hint">
            {{ diagram.node_count }} élément{{ diagram.node_count > 1 ? 's' : '' }}
          </span>
          <span v-if="pathOf(diagram.package_id)" class="hint">{{ pathOf(diagram.package_id) }}</span>
          <select
            v-if="canWrite"
            :aria-label="`Ranger ${diagram.name} dans…`"
            :value="diagram.package_id ?? ROOT"
            @change="moveDiagram(diagram, ($event.target as HTMLSelectElement).value)"
          >
            <option :value="ROOT">Racine (sans paquet)</option>
            <option v-for="entry in packages.packages.value" :key="entry.id" :value="entry.id">
              {{ entry.path }}
            </option>
          </select>
          <button v-if="canWrite" type="button" class="secondary" @click="confirming = diagram">
            Supprimer
          </button>
        </li>
      </ul>
    </section>
  </div>

  <section v-else class="diagrams">
    <header class="editor__header">
      <RouterLink :to="{ query: {} }">← Tous les diagrammes</RouterLink>
      <h2 v-if="editor.diagram.value">{{ editor.diagram.value.name }}</h2>
      <p v-if="canWrite" class="hint" aria-live="polite">{{ saveState }}</p>
    </header>

    <p v-if="editor.status.value === 'error'" class="banner banner--error" role="alert">
      {{ editor.error.value }}
    </p>
    <p v-else-if="editor.status.value === 'loading'" class="hint">Chargement du diagramme…</p>
    <p v-if="editor.saveError.value" class="banner banner--error" role="alert">
      Disposition non enregistrée : {{ editor.saveError.value }}
    </p>
    <p v-if="editor.conflict.value && !editor.reloaded.value" class="banner banner--error" role="alert">
      Quelqu'un a enregistré ce diagramme avant toi : tes modifications n'ont pas été envoyées.
      <button type="button" @click="reload">Recharger sa version</button>
      <button type="button" class="secondary" @click="editor.discardMine()">
        Abandonner mes modifications
      </button>
    </p>
    <p v-if="editor.reloaded.value" class="banner" role="status">
      Sa version est chargée.
      <button type="button" @click="editor.pasteMine()">Recoller mes modifications</button>
      <button type="button" class="secondary" @click="editor.discardMine()">Les abandonner</button>
    </p>

    <div
      v-if="editor.diagram.value"
      class="editor diagram-editor"
      :class="{ 'editor--reading': !canWrite }"
    >
      <div v-if="canWrite" class="editor__left">
        <ElementPalette
          :items="palette.items.value"
          :total="palette.total.value"
          :placed="editor.placed.value"
          :error="palette.error.value"
          :type-label="metamodel.labelOf"
          @search="(term) => palette.search(term)"
          @add="onAdd"
        />
        <AnnotationPalette @add-note="addAnnotation('note')" @add-group="addAnnotation('group')" />
      </div>

      <div class="editor__centre">
        <LinkPopover
          v-if="link && canWrite"
          :key="`${link.source.id}-${link.target.id}`"
          :source="link.source"
          :target="link.target"
          :connect="editor.connect"
          @done="link = null"
          @cancel="link = null"
        />
        <DiagramToolbar
          :can-write="canWrite"
          :can-undo="editor.canUndo.value"
          :can-redo="editor.canRedo.value"
          :selection-count="selection.ids.value.length"
          @undo="editor.undo"
          @redo="editor.redo"
          @align="arrange"
          @distribute="spread"
          @export="exportAs"
        />
        <p v-if="exportError" class="banner banner--error" role="alert">{{ exportError }}</p>
        <DiagramCanvas
          :boxes="editor.boxes.value"
          :relationships="editor.relationships.value"
          :selected-id="selectedId"
          :type-label="metamodel.labelOf"
          :readonly="!canWrite"
          :inactive-ids="editor.inactiveIds.value"
          :selection="selection.ids.value"
          :objects="editor.objects.value"
          :routed="editor.edges.value"
          :selected-object-id="selectedObjectId"
          :selected-edge-id="selectedEdgeId"
          @place="onPlace"
          @move="editor.move"
          @resize="editor.resize"
          @moved="editor.endGesture"
          @select="onSelect"
          @toggle="selection.toggle"
          @select-many="onSelectMany"
          @remove-many="removeSelected"
          @remove="removeBox"
          @connect="onConnect"
          @select-object="selectObject"
          @select-edge="selectEdge"
          @move-object="editor.moveObject"
          @resize-object="editor.resizeObject"
          @remove-object="editor.removeObject"
          @bend-add="editor.addWaypoint"
          @bend-move="editor.moveWaypoint"
          @bend-remove="editor.removeWaypoint"
        />
      </div>

      <aside class="editor__detail">
        <ObjectInspector
          :item="selectedObject"
          :edge="selectedEdge"
          :anchors="anchorChoices"
          :children="childrenOfSelectedGroup"
          :can-write="canWrite"
          @update="(patch) => editor.updateObject(selectedObjectId, patch)"
          @remove="removeSelectedObject"
          @straighten="editor.straighten(selectedEdgeId)"
          @link="onLinkContent"
        />
        <p v-if="!selectedId" class="hint">
          Sélectionne un élément pour voir son détail. Tire la poignée d'un élément sélectionné
          jusqu'à un autre pour les relier.
        </p>
        <p v-else-if="detail.status.value === 'loading'" class="hint">Chargement de l'élément…</p>
        <p v-else-if="detail.status.value === 'error'" class="banner banner--error" role="alert">
          {{ detail.error.value }}
        </p>
        <template v-if="detail.element.value">
          <button v-if="canWrite && editor.placed.value.has(selectedId)" type="button"
                  @click="removeBox(selectedId)">
            Retirer du diagramme
          </button>
          <ElementDetail
            :key="detail.element.value.id"
            :element="detail.element.value"
            :type-label="metamodel.labelOf(detail.element.value.element_type)"
            @close="deselect"
          />
        </template>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.browser {
  display: grid;
  grid-template-columns: minmax(12rem, 16rem) minmax(0, 1fr);
  gap: 1.2rem;
  align-items: start;
}
.browser__tree {
  display: grid;
  gap: 0.8rem;
}
@media (max-width: 48rem) {
  .browser {
    grid-template-columns: minmax(0, 1fr);
  }
}
.diagrams {
  display: grid;
  gap: 1rem;
}
header h2 {
  margin: 0 0 0.2rem;
}
.editor__header {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.4rem 1rem;
}
.editor__header h2 {
  margin: 0;
}
.editor {
  display: grid;
  grid-template-columns: 12rem minmax(0, 1fr) 16rem;
  align-items: start;
  gap: 1rem;
}
.editor--reading {
  grid-template-columns: minmax(0, 1fr) 16rem;
}
.editor__centre,
.editor__left {
  display: grid;
  gap: 1rem;
  min-width: 0;
  align-content: start;
}
.editor__detail {
  display: grid;
  gap: 0.6rem;
  min-width: 0;
}
@media (max-width: 64rem) {
  .editor {
    grid-template-columns: 12rem minmax(0, 1fr);
  }
  .editor__detail {
    grid-column: 1 / -1;
  }
}
.create {
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
button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.secondary {
  background: transparent;
}
.list {
  display: grid;
  gap: 0.4rem;
  margin: 0;
  padding: 0;
  list-style: none;
}
.list li {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.5rem 0.7rem;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.list li a {
  flex: 1;
  font-weight: 600;
}
.hint {
  margin: 0;
  opacity: 0.75;
}
.banner {
  margin: 0;
  padding: 0.6rem 0.9rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.6rem;
}
.banner--error {
  border-color: #c62828;
  color: #c62828;
}
.banner--confirm {
  border-color: #ef6c00;
}
</style>
