<script setup lang="ts">
// L'état d'approbation d'un élément, et les seuls boutons que l'API offre à cet
// appelant (`allowed_transitions`). Le SPA masque ; l'API décide (docs/adr/0046).
import { computed, ref } from 'vue'

import { ApiError, messageOf } from '../../lib/api'
import { STATE_LABELS, TRANSITION_LABELS, type Transition } from './approvalLabels'
import { useApproval } from './useApproval'
import type { ElementRead } from './useElementCatalogue'

const props = defineProps<{ element: ElementRead }>()
const emit = defineEmits<{ changed: [element: ElementRead] }>()

const approval = useApproval()
const failure = ref('')
const busy = ref(false)

const label = computed(() =>
  props.element.locked ? 'Verrouillé' : STATE_LABELS[props.element.approval_state],
)

/** Why a write may be refused, said before it is tried. The API still decides. */
const why = computed(() => {
  if (props.element.locked) {
    return 'Verrouillé : aucune modification, par personne, avant le déverrouillage.'
  }
  if (props.element.approval_state === 'approved') {
    return 'Approuvé : seul un approbateur le modifie, et il repasse alors en revue.'
  }
  return ''
})

async function take(next: Transition): Promise<void> {
  busy.value = true
  failure.value = ''
  try {
    emit('changed', await approval.transition(props.element.id, next, props.element.revision))
  } catch (caught) {
    failure.value = messageOf(caught)
    if (caught instanceof ApiError && caught.code === 'approval_stale') {
      // The content moved while the approver read it: show the new content, say
      // so, and leave the approving to a person who has read it. Never retried.
      failure.value =
        'Le contenu a changé depuis votre lecture : relisez la fiche avant d’approuver.'
      const fresh = await approval.reread(props.element.id)
      if (fresh) {
        emit('changed', fresh)
      }
    }
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section
    class="banner"
    :data-state="element.locked ? 'locked' : element.approval_state"
    aria-label="État d'approbation"
  >
    <strong>{{ label }}</strong>
    <span class="meta">révision {{ element.revision }}</span>
    <span v-if="element.approved_by" class="meta">
      approuvé par {{ element.approved_by
      }}<template v-if="element.approved_at"> le {{ element.approved_at.slice(0, 10) }}</template>
    </span>
    <button
      v-for="next in element.allowed_transitions"
      :key="next"
      type="button"
      :disabled="busy"
      @click="take(next)"
    >
      {{ TRANSITION_LABELS[next] }}
    </button>
    <p v-if="why" class="why" role="status">{{ why }}</p>
    <p v-if="failure" class="failure" role="alert">{{ failure }}</p>
  </section>
</template>

<style scoped>
.banner {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  align-items: baseline;
  padding: 0.5rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.banner[data-state='approved'],
.banner[data-state='locked'] {
  border-color: var(--success, #1b6e3a);
}
.banner[data-state='in_review'] {
  border-color: var(--warning, #9a6700);
}
.meta {
  font-size: 0.9rem;
}
.why,
.failure {
  flex-basis: 100%;
  margin: 0;
  font-size: 0.9rem;
}
</style>
