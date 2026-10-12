<script setup lang="ts">
// What can be said about the selected note, group or routed link.
//
// It decides nothing: it names the change the user made. "Relier le contenu…"
// is the only road from a group to a relationship, and it opens the usual link
// form for a pair the user picked — one explicit gesture per relationship, each
// validated by the API (docs/adr/0069).
import { ref } from 'vue'

import type { DiagramObject } from './useDiagram'

defineProps<{
  item?: DiagramObject
  edge?: { relationshipId: string; label: string; bends: number }
  /** Boxes a note can be anchored on. */
  anchors: { id: string; name: string }[]
  /** Boxes drawn inside the selected group. */
  children: { id: string; name: string }[]
  canWrite: boolean
}>()

const emit = defineEmits<{
  update: [patch: { text?: string; colour?: string | null; anchor_element_id?: string | null }]
  remove: []
  straighten: []
  link: [sourceId: string, targetId: string]
}>()

const source = ref('')
const target = ref('')
const COLOURS = ['#fff4b0', '#ffe0e0', '#d6f0ff', '#d9f5d0', '#efeaf8']
</script>

<template>
  <section v-if="item" class="inspector" :aria-label="item.kind === 'note' ? 'Note' : 'Groupe'">
    <template v-if="item.kind === 'note'">
      <label for="object-text">Texte de la note</label>
      <textarea
        id="object-text"
        :value="item.text"
        :disabled="!canWrite"
        maxlength="2000"
        rows="4"
        @input="emit('update', { text: ($event.target as HTMLTextAreaElement).value })"
      />
      <label for="object-anchor">Ancrer sur un élément</label>
      <select
        id="object-anchor"
        :value="item.anchor_element_id ?? ''"
        :disabled="!canWrite"
        @change="
          emit('update', {
            anchor_element_id: ($event.target as HTMLSelectElement).value || null,
          })
        "
      >
        <option value="">Aucun</option>
        <option v-for="anchor in anchors" :key="anchor.id" :value="anchor.id">{{ anchor.name }}</option>
      </select>
    </template>
    <template v-else>
      <label for="object-text">Titre du groupe</label>
      <input
        id="object-text"
        :value="item.text"
        :disabled="!canWrite"
        maxlength="2000"
        @input="emit('update', { text: ($event.target as HTMLInputElement).value })"
      />
      <p class="hint">
        Un groupe ne dit rien du modèle : le retirer ne supprime aucun élément, et les éléments qu'il
        contient restent sur le diagramme.
      </p>
      <fieldset v-if="canWrite" class="pair">
        <legend>Relier le contenu</legend>
        <label for="pair-source">Source</label>
        <select id="pair-source" v-model="source">
          <option value="">—</option>
          <option v-for="child in children" :key="child.id" :value="child.id">{{ child.name }}</option>
        </select>
        <label for="pair-target">Cible</label>
        <select id="pair-target" v-model="target">
          <option value="">—</option>
          <option v-for="child in children" :key="child.id" :value="child.id">{{ child.name }}</option>
        </select>
        <button
          type="button"
          :disabled="!source || !target || source === target"
          @click="emit('link', source, target)"
        >
          Relier le contenu…
        </button>
      </fieldset>
    </template>

    <div v-if="canWrite" class="colours" role="group" aria-label="Couleur">
      <button
        v-for="colour in COLOURS"
        :key="colour"
        type="button"
        class="swatch"
        :style="{ background: colour }"
        :aria-label="`Couleur ${colour}`"
        :aria-pressed="item.colour === colour"
        @click="emit('update', { colour })"
      />
      <button type="button" class="secondary" @click="emit('update', { colour: null })">Défaut</button>
    </div>

    <button v-if="canWrite" type="button" class="secondary" @click="emit('remove')">
      {{ item.kind === 'note' ? 'Supprimer la note' : 'Retirer le groupe' }}
    </button>
  </section>

  <section v-else-if="edge" class="inspector" aria-label="Lien">
    <p>
      Lien « {{ edge.label }} » — {{ edge.bends }} coude{{ edge.bends > 1 ? 's' : '' }}.
    </p>
    <button
      v-if="canWrite"
      type="button"
      :disabled="edge.bends === 0"
      @click="emit('straighten')"
    >
      Redresser le lien
    </button>
  </section>
</template>

<style scoped>
.inspector {
  display: grid;
  gap: 0.4rem;
}
.pair {
  display: grid;
  gap: 0.3rem;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.colours {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
}
.swatch {
  width: 1.4rem;
  height: 1.4rem;
  border: 1px solid var(--border);
  border-radius: 4px;
  cursor: pointer;
}
</style>
