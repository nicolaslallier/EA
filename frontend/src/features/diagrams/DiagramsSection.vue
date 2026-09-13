<script setup lang="ts">
// The `/diagrammes` section: the saved diagrams, and the editor of one.
//
// Which diagram is open and which element is selected live in the URL
// (`?diagram=`, `?element=`), so an editor can be linked to and the back button
// leaves a diagram. Opening a diagram pushes a history entry; selecting an
// element only replaces one, or every click would bury the list under it.
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { messageOf } from '../../lib/api'
import type { Point } from '../../lib/diagramGeometry'
import { BOX_HEIGHT } from '../../lib/graphLayout'
import ElementDetail from '../elements/ElementDetail.vue'
import { useElementDetail } from '../elements/useElementDetail'
import { useMetamodel } from '../metamodel/useMetamodel'
import DiagramCanvas from './DiagramCanvas.vue'
import ElementPalette from './ElementPalette.vue'
import LinkPopover from './LinkPopover.vue'
import { useDiagram, useElementPalette, type ElementRead } from './useDiagram'
import { useDiagrams, type DiagramSummaryRead } from './useDiagrams'

const route = useRoute()
const router = useRouter()
const list = useDiagrams()
const editor = useDiagram()
const palette = useElementPalette()
const metamodel = useMetamodel()
const detail = useElementDetail()

/** A query parameter is `string | string[] | null`; only one value means anything here. */
function one(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

const diagramId = computed(() => one(route.query.diagram))
const selectedId = computed(() => one(route.query.element))

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
  await metamodel.load()
})

watch(
  diagramId,
  async (id) => {
    link.value = null
    if (!id) {
      editor.close()
      await list.load()
      return
    }
    await Promise.all([
      editor.open(id),
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
    const created = await list.create(name)
    newName.value = ''
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
</script>

<template>
  <section v-if="!diagramId" class="diagrams">
    <header>
      <h2>Diagrammes</h2>
      <p class="hint">Des vues enregistrées : quels éléments, et où. Les faits restent dans le modèle.</p>
    </header>

    <form class="create" aria-label="Créer un diagramme" @submit.prevent="create">
      <div class="field">
        <label for="diagram-name">Nom du diagramme</label>
        <input id="diagram-name" v-model="newName" maxlength="200" autocomplete="off" />
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
        <button type="button" class="secondary" @click="confirming = diagram">Supprimer</button>
      </li>
    </ul>
  </section>

  <section v-else class="diagrams">
    <header class="editor__header">
      <RouterLink :to="{ query: {} }">← Tous les diagrammes</RouterLink>
      <h2 v-if="editor.diagram.value">{{ editor.diagram.value.name }}</h2>
      <p class="hint" aria-live="polite">{{ saveState }}</p>
    </header>

    <p v-if="editor.status.value === 'error'" class="banner banner--error" role="alert">
      {{ editor.error.value }}
    </p>
    <p v-else-if="editor.status.value === 'loading'" class="hint">Chargement du diagramme…</p>
    <p v-if="editor.saveError.value" class="banner banner--error" role="alert">
      Disposition non enregistrée : {{ editor.saveError.value }}
    </p>

    <div v-if="editor.diagram.value" class="editor diagram-editor">
      <ElementPalette
        :items="palette.items.value"
        :total="palette.total.value"
        :placed="editor.placed.value"
        :error="palette.error.value"
        :type-label="metamodel.labelOf"
        @search="(term) => palette.search(term)"
        @add="onAdd"
      />

      <div class="editor__centre">
        <LinkPopover
          v-if="link"
          :key="`${link.source.id}-${link.target.id}`"
          :source="link.source"
          :target="link.target"
          :connect="editor.connect"
          @done="link = null"
          @cancel="link = null"
        />
        <DiagramCanvas
          :boxes="editor.boxes.value"
          :relationships="editor.relationships.value"
          :selected-id="selectedId"
          :type-label="metamodel.labelOf"
          @place="onPlace"
          @move="editor.move"
          @moved="editor.persist"
          @select="select"
          @remove="removeBox"
          @connect="onConnect"
        />
      </div>

      <aside class="editor__detail">
        <p v-if="!selectedId" class="hint">
          Sélectionne un élément pour voir son détail. Tire la poignée d'un élément sélectionné
          jusqu'à un autre pour les relier.
        </p>
        <p v-else-if="detail.status.value === 'loading'" class="hint">Chargement de l'élément…</p>
        <p v-else-if="detail.status.value === 'error'" class="banner banner--error" role="alert">
          {{ detail.error.value }}
        </p>
        <template v-if="detail.element.value">
          <button v-if="editor.placed.value.has(selectedId)" type="button"
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
.editor__centre,
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
input {
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
