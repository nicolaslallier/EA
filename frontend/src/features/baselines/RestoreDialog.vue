<script setup lang="ts">
// Restaurer une baseline : un essai à blanc d'abord, la saisie du nom ensuite.
//
// Le plan vient de l'API (`dry_run`) ; ce composant l'affiche, il ne décide de
// rien. Pour appliquer, il renvoie le `digest` du plan qu'il a montré : si le
// modèle a bougé depuis, l'API refuse (`restore_conflict`) et la boîte relit le
// plan au lieu d'appliquer autre chose que ce que la personne a lu. Le bouton
// reste inactif tant que le nom exact n'est pas saisi, tant qu'un élément
// protégé bloque, et pendant l'appel (pas de double envoi). Un échec ne ferme
// jamais la boîte.
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

import { ApiError, messageOf } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import { useBaselines, type Baseline, type RestoreResult } from './useBaselines'

const props = defineProps<{
  baseline: Baseline | { id: string; name: string }
}>()
const emit = defineEmits<{ close: []; restored: [] }>()

const store = useBaselines()
const reading = useLatestRequest()
const result = ref<RestoreResult | null>(null)
const typed = ref('')
const failure = ref('')
const busy = ref(false)
const dialog = ref<HTMLElement | null>(null)
let opener: Element | null = null

const plan = computed(() => result.value?.plan ?? null)
const canApply = computed(
  () =>
    typed.value === props.baseline.name && plan.value !== null && plan.value.blocked_count === 0,
)

/** « et 3 autres » — the lists name the first few, the counts are exact. */
function more(total: number, listed: number, noun = ''): string {
  const rest = total - listed
  const others = rest > 1 ? 'autres' : 'autre'
  return rest > 0 ? `et ${rest} ${others}${noun ? ` ${noun}${rest > 1 ? 's' : ''}` : ''}` : ''
}

async function read(): Promise<void> {
  typed.value = ''
  result.value = null
  await reading.run(
    (signal) => store.restore(props.baseline.id, { dry_run: true }, signal),
    (answer) => {
      result.value = answer
    },
  )
  await keepFocusInside()
}

onMounted(() => {
  opener = document.activeElement
  dialog.value?.focus()
  void read()
})

onBeforeUnmount(() => {
  if (opener instanceof HTMLElement) {
    opener.focus()
  }
})

async function apply(): Promise<void> {
  const read_ = result.value
  if (!canApply.value || busy.value || read_ === null) {
    return
  }
  busy.value = true
  failure.value = ''
  try {
    await store.restore(props.baseline.id, {
      dry_run: false,
      expected_digest: read_.digest,
    })
    emit('restored')
  } catch (caught) {
    failure.value = explain(caught)
    if (
      caught instanceof ApiError &&
      ['restore_conflict', 'restore_blocked'].includes(caught.code)
    ) {
      // What was confirmed is no longer what would happen: show what would.
      await read()
    }
  } finally {
    busy.value = false
  }
  await keepFocusInside()
}

function explain(caught: unknown): string {
  if (caught instanceof ApiError && caught.code === 'restore_conflict') {
    return "L'état du modèle a changé depuis le plan affiché. Le plan est relu : vérifie-le ; la saisie du nom a été vidée, retape le nom de la baseline pour confirmer."
  }
  if (caught instanceof ApiError && caught.code === 'restore_blocked') {
    return `Un élément verrouillé empêche la restauration (${caught.message}). Déverrouille-le, puis relis le plan.`
  }
  return messageOf(caught)
}

function focusables(): HTMLElement[] {
  return Array.from(
    dialog.value?.querySelectorAll<HTMLElement>('input:not(:disabled), button:not(:disabled)') ??
      [],
  )
}

/** A control that vanishes or is disabled would drop the focus on the page behind. */
async function keepFocusInside(): Promise<void> {
  await nextTick()
  if (dialog.value && !dialog.value.contains(document.activeElement)) {
    ;(focusables()[0] ?? dialog.value).focus()
  }
}

function close(): void {
  if (!busy.value) {
    emit('close')
  }
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.preventDefault()
    close()
    return
  }
  if (event.key !== 'Tab') {
    return
  }
  const controls = focusables()
  const first = controls[0]
  const last = controls.at(-1)
  if (first === undefined || last === undefined) {
    event.preventDefault()
    return
  }
  if (
    event.shiftKey &&
    (document.activeElement === first || document.activeElement === dialog.value)
  ) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}
</script>

<template>
  <div class="backdrop">
    <section
      ref="dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="restore-title"
      tabindex="-1"
      class="dialog"
      @keydown="onKeydown"
    >
      <h3 id="restore-title">Restaurer « {{ baseline.name }} »</h3>
      <p v-if="failure" role="alert">{{ failure }}</p>
      <p v-if="reading.status.value === 'loading'" role="status">Calcul du plan…</p>
      <p v-else-if="reading.status.value === 'error'" role="alert">
        {{ reading.error.value }}
      </p>

      <template v-if="plan">
        <p class="documents">
          Les documents ne sont pas capturés dans une baseline : ceux d'un élément supprimé par
          cette restauration sont perdus définitivement, et ceux d'un élément recréé ne reviendront
          pas.
        </p>
        <dl class="counts">
          <dt>Éléments recréés</dt>
          <dd>{{ plan.elements_to_create }}</dd>
          <dt>Éléments remis à l’état de la baseline</dt>
          <dd>{{ plan.elements_to_update }}</dd>
          <dt>Éléments supprimés</dt>
          <dd>{{ plan.elements_to_delete }}</dd>
          <dt>Relations recréées</dt>
          <dd>{{ plan.relationships_to_create }}</dd>
          <dt>Relations supprimées</dt>
          <dd>{{ plan.relationships_to_delete }}</dd>
          <dt>Relations supprimées avec leurs éléments</dt>
          <dd>{{ plan.relationships_cascaded }}</dd>
          <dt>Boîtes de diagramme remises en place</dt>
          <dd>{{ plan.boxes_to_restore }}</dd>
          <dt class="loss">Documents perdus</dt>
          <dd class="loss">{{ plan.documents_lost }}</dd>
          <dt class="loss">Boîtes de diagramme perdues</dt>
          <dd class="loss">{{ plan.boxes_lost }}</dd>
        </dl>

        <template
          v-for="[title, listed, total] in [
            ['Recréés', plan.created, plan.elements_to_create],
            ['Remis à l’état de la baseline', plan.updated, plan.elements_to_update],
            ['Supprimés', plan.deleted, plan.elements_to_delete],
          ] as const"
          :key="title"
        >
          <div v-if="listed.length" class="named">
            <h4>{{ title }}</h4>
            <ul>
              <li v-for="entity in listed" :key="entity.id">
                {{ entity.label }}
              </li>
            </ul>
            <p v-if="total > listed.length">{{ more(total, listed.length) }}</p>
          </div>
        </template>

        <div v-if="plan.warnings.length" class="named">
          <h4>Avertissements</h4>
          <ul>
            <li v-for="warning in plan.warnings" :key="warning">
              {{ warning }}
            </li>
          </ul>
          <p v-if="plan.warning_count > plan.warnings.length">
            {{ more(plan.warning_count, plan.warnings.length, 'avertissement') }}
          </p>
        </div>

        <div v-if="plan.blocked_count > 0" class="blocked">
          <h4>Éléments protégés : la restauration est impossible</h4>
          <ul>
            <li v-for="entity in plan.blocked" :key="entity.id">
              {{ entity.label }}
            </li>
          </ul>
          <p v-if="plan.blocked_count > plan.blocked.length">
            {{ more(plan.blocked_count, plan.blocked.length) }}
          </p>
          <p>Déverrouille-les, puis rouvre cette boîte.</p>
        </div>

        <label>
          Saisir le nom de la baseline pour confirmer
          <input v-model="typed" type="text" autocomplete="off" :disabled="busy" />
        </label>
      </template>

      <p class="actions">
        <button type="button" class="secondary" :disabled="busy" @click="close">Annuler</button>
        <button type="button" :disabled="!canApply || busy" @click="apply">Restaurer</button>
      </p>
    </section>
  </div>
</template>

<style scoped>
.backdrop {
  position: fixed;
  inset: 0;
  background: rgb(0 0 0 / 40%);
  overflow-y: auto;
  z-index: 10;
}
.dialog {
  max-width: 42rem;
  margin: 5vh auto;
  padding: 1rem 1.2rem;
  background: var(--bg, canvas);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 8px 30px rgb(0 0 0 / 25%);
}
.counts {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 0.2rem 1rem;
}
.counts dd {
  margin: 0;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.loss,
.documents {
  font-weight: 600;
}
.named ul,
.blocked ul {
  margin: 0.2rem 0;
  padding-left: 1.2rem;
  overflow-wrap: anywhere;
}
.actions {
  display: flex;
  gap: 0.6rem;
  justify-content: end;
}
</style>
