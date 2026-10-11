<script setup lang="ts">
// The editor's tool strip: take back, arrange a selection, save as an image.
//
// It decides nothing — it names the gesture the user chose. Everything that
// changes the diagram is hidden from a reader (the API refuses it anyway,
// docs/adr/0032); exporting only reads, so a reader keeps it.
import type { AlignMode, DistributeAxis } from '../../lib/diagramGeometry'
import type { ExportFormat } from '../../lib/diagramExport'

defineProps<{
  canWrite: boolean
  canUndo: boolean
  canRedo: boolean
  selectionCount: number
}>()

const emit = defineEmits<{
  undo: []
  redo: []
  align: [mode: AlignMode]
  distribute: [axis: DistributeAxis]
  export: [format: ExportFormat]
}>()

const ALIGNMENTS: { mode: AlignMode; label: string; glyph: string }[] = [
  { mode: 'left', label: 'Aligner à gauche', glyph: '⇤' },
  { mode: 'centre', label: 'Centrer horizontalement', glyph: '⇹' },
  { mode: 'right', label: 'Aligner à droite', glyph: '⇥' },
  { mode: 'top', label: 'Aligner en haut', glyph: '⤒' },
  { mode: 'middle', label: 'Centrer verticalement', glyph: '↕' },
  { mode: 'bottom', label: 'Aligner en bas', glyph: '⤓' },
]
</script>

<template>
  <div class="toolbar" role="toolbar" aria-label="Outils du diagramme">
    <div v-if="canWrite" class="toolbar__group" role="group" aria-label="Historique">
      <button type="button" :disabled="!canUndo" @click="emit('undo')">Annuler</button>
      <button type="button" :disabled="!canRedo" @click="emit('redo')">Rétablir</button>
    </div>

    <div v-if="canWrite" class="toolbar__group" role="group" aria-label="Alignement">
      <button
        v-for="alignment in ALIGNMENTS"
        :key="alignment.mode"
        type="button"
        :aria-label="alignment.label"
        :title="alignment.label"
        :disabled="selectionCount < 2"
        @click="emit('align', alignment.mode)"
      >
        {{ alignment.glyph }}
      </button>
      <button
        type="button"
        aria-label="Répartir horizontalement"
        :disabled="selectionCount < 3"
        @click="emit('distribute', 'horizontal')"
      >
        ⇔
      </button>
      <button
        type="button"
        aria-label="Répartir verticalement"
        :disabled="selectionCount < 3"
        @click="emit('distribute', 'vertical')"
      >
        ⇕
      </button>
    </div>

    <div class="toolbar__group" role="group" aria-label="Exporter">
      <button type="button" aria-label="Exporter en SVG" @click="emit('export', 'svg')">SVG</button>
      <button type="button" aria-label="Exporter en PNG" @click="emit('export', 'png')">PNG</button>
      <button type="button" aria-label="Exporter en PNG 2x" @click="emit('export', 'png2')">
        PNG 2x
      </button>
    </div>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem 1rem;
}
.toolbar__group {
  display: flex;
  gap: 0.25rem;
}
</style>
