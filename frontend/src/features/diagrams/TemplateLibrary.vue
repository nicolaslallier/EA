<script setup lang="ts">
// The saved templates: what each holds, and the one to build a diagram from.
// Using or deleting one writes (a diagram, a template), so a reader gets the list alone.
import type { DiagramTemplateSummaryRead } from './useTemplates'

defineProps<{ templates: DiagramTemplateSummaryRead[]; canWrite: boolean }>()
const emit = defineEmits<{ use: [id: string]; remove: [id: string] }>()
</script>

<template>
  <section class="library" aria-label="Modèles de diagrammes">
    <h3>Modèles</h3>
    <p v-if="templates.length === 0" class="hint">
      Aucun modèle. Enregistre un diagramme comme modèle pour en créer un.
    </p>
    <ul>
      <li v-for="template in templates" :key="template.id">
        <strong>{{ template.name }}</strong>
        <span class="hint">
          {{ template.slot_count }} emplacement{{ template.slot_count > 1 ? 's' : '' }},
          {{ template.group_count }} groupe{{ template.group_count > 1 ? 's' : '' }},
          {{ template.link_count }} lien{{ template.link_count > 1 ? 's' : '' }}
        </span>
        <span v-if="template.description" class="hint">{{ template.description }}</span>
        <template v-if="canWrite">
          <button
            type="button"
            :aria-label="`Utiliser ${template.name}`"
            @click="emit('use', template.id)"
          >
            Utiliser
          </button>
          <button
            type="button"
            class="secondary"
            :aria-label="`Supprimer ${template.name}`"
            @click="emit('remove', template.id)"
          >
            Supprimer
          </button>
        </template>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.library li {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.5rem;
}
</style>
