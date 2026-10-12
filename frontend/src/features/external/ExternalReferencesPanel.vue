<script setup lang="ts">
// What an element cites in other tools — a ticket, a page — with the title and state the server
// cached (docs/adr/0073).
//
// The server does everything that is a rule: how an identifier is spelled, which targets exist,
// what a provider says. This lists, asks and shows the answer. A title is a third party's text:
// it is only ever interpolated as text, and an address becomes a link only when it is http(s).
import { computed, onMounted, ref } from 'vue'

import { messageOf } from '../../lib/api'
import { useMe } from '../../lib/me'
import type { ElementRead } from '../elements/useElementCatalogue'
import {
  KIND_LABELS,
  STATUS_LABELS,
  ago,
  safeHref,
  useExternalReferences,
  type ExternalIssue,
  type ExternalKind,
} from './useExternalReferences'

const props = defineProps<{ element: ElementRead }>()
defineEmits<{ close: [] }>()

const { canWrite } = useMe()
const references = useExternalReferences()
const failure = ref('')
const busy = ref(false)
const confirming = ref<string | null>(null)

const FREE_LINK = 'url'
/** `kind` is the target's, or `url` for a free link: the choice carries both. */
const choice = ref('')
const text = ref('')

onMounted(async () => {
  await references.load(props.element.id)
  choice.value = activeTargets.value[0]?.name ?? FREE_LINK
})

const activeTargets = computed(() => references.targets.value.filter((target) => target.active))
const writableTargets = computed(() => activeTargets.value.filter((target) => target.writable))
const anyStale = computed(() => references.items.value.some((item) => item.stale))

function chosen(): { kind: ExternalKind; target: string | null } {
  if (choice.value === FREE_LINK) {
    return { kind: 'url', target: null }
  }
  const target = activeTargets.value.find((candidate) => candidate.name === choice.value)
  return { kind: (target?.kind ?? 'url'), target: target?.name ?? null }
}

async function guarded(action: () => Promise<void>): Promise<void> {
  busy.value = true
  failure.value = ''
  try {
    await action()
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

async function add(): Promise<void> {
  const { kind, target } = chosen()
  await guarded(async () => {
    await references.link(props.element.id, kind, text.value, target)
    text.value = ''
  })
}

async function confirmRemove(): Promise<void> {
  const id = confirming.value
  if (!id) {
    return
  }
  await guarded(async () => {
    await references.unlink(props.element.id, id)
    confirming.value = null
  })
}

// --- Opening a ticket: preview first, send second -----------------------------------------
const issueTarget = ref('')
const issueScope = ref('')
const issueTitle = ref('')
const issueBody = ref('')
const preview = ref<ExternalIssue | null>(null)
const opened = ref<ExternalIssue | null>(null)

async function showPreview(): Promise<void> {
  opened.value = null
  await guarded(async () => {
    preview.value = await references.issue(props.element.id, fields(), true)
  })
}

async function sendIssue(): Promise<void> {
  await guarded(async () => {
    opened.value = await references.issue(props.element.id, fields(), false)
    preview.value = null
  })
}

function fields() {
  return {
    target: issueTarget.value || (writableTargets.value[0]?.name ?? ''),
    scope: issueScope.value,
    title: issueTitle.value,
    body: issueBody.value,
  }
}

function labelOf(kind: string): string {
  return KIND_LABELS[kind] ?? kind
}
</script>

<template>
  <section class="references" aria-label="Références de l'élément">
    <header class="references__header">
      <h3>Références de « {{ element.name }} »</h3>
      <button type="button" class="secondary" @click="$emit('close')">Fermer</button>
    </header>

    <p class="hint">
      Les objets d'autres outils (tickets, pages) que cet élément cite. Titre et état viennent du
      cache du serveur ; ouvrir cet écran n'appelle aucun fournisseur.
    </p>

    <p v-if="anyStale" class="banner" role="status">
      Fournisseur injoignable ou cache ancien — titre et état affichés viennent du cache.
    </p>
    <p v-if="failure" class="banner banner--error" role="alert">{{ failure }}</p>
    <p v-if="references.status.value === 'error'" class="banner banner--error" role="alert">
      {{ references.error.value }}
    </p>

    <p v-else-if="references.status.value !== 'ready'" class="hint">Chargement…</p>

    <p v-else-if="references.items.value.length === 0" class="hint">
      Aucune référence pour l'instant.
    </p>

    <table v-else>
      <caption class="sr-only">
        Références de l'élément
      </caption>
      <thead>
        <tr>
          <th scope="col">Type</th>
          <th scope="col">Objet</th>
          <th scope="col">État</th>
          <th scope="col">Mis à jour</th>
          <th v-if="canWrite" scope="col">Actions</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in references.items.value" :key="item.id">
          <td>{{ labelOf(item.kind) }}</td>
          <td>
            <a
              v-if="safeHref(item.url)"
              :href="safeHref(item.url)"
              target="_blank"
              rel="noopener noreferrer"
            >{{ item.title || item.external_id }}</a
            >
            <span v-else>{{ item.title || item.external_id }}</span>
            <small v-if="item.title" class="id">{{ item.external_id }}</small>
          </td>
          <td>
            <span :class="['pill', `pill--${item.status}`]">{{
              STATUS_LABELS[item.status] ?? item.status
            }}</span>
          </td>
          <td>{{ ago(item.synced_at) || '—' }}</td>
          <td v-if="canWrite">
            <button
              v-if="item.kind !== FREE_LINK"
              type="button"
              class="secondary"
              :disabled="busy"
              :aria-label="`Actualiser ${item.title || item.external_id}`"
              @click="guarded(() => references.refresh(element.id, item.id))"
            >
              Actualiser
            </button>
            <button
              type="button"
              class="secondary"
              :aria-label="`Retirer ${item.title || item.external_id}`"
              @click="confirming = item.id"
            >
              Retirer
            </button>
          </td>
        </tr>
      </tbody>
    </table>

    <p v-if="confirming" class="banner banner--confirm" role="alert">
      Retirer ce lien ? L'objet de l'autre outil n'est pas touché.
      <button type="button" :disabled="busy" @click="confirmRemove">Confirmer</button>
      <button type="button" class="secondary" @click="confirming = null">Renoncer</button>
    </p>

    <template v-if="canWrite">
      <p v-if="references.items.value.some((i) => i.kind !== FREE_LINK)">
        <button
          type="button"
          class="secondary"
          :disabled="busy"
          @click="guarded(() => references.refreshAll(element.id))"
        >
          Tout actualiser
        </button>
      </p>

      <form class="add" @submit.prevent="add">
        <label for="reference-target">Cible</label>
        <select id="reference-target" v-model="choice">
          <option v-for="target in activeTargets" :key="target.name" :value="target.name">
            {{ target.name }} — {{ labelOf(target.kind) }}
          </option>
          <option :value="FREE_LINK">Lien libre (url)</option>
        </select>
        <label for="reference-text">Référence</label>
        <input id="reference-text" v-model="text" type="text" maxlength="2048" required />
        <button type="submit" :disabled="busy || !text.trim()">Ajouter</button>
      </form>

      <details v-if="writableTargets.length > 0" class="issue">
        <summary>Ouvrir un ticket depuis cet élément</summary>
        <form @submit.prevent="showPreview">
          <label for="issue-target">Cible du ticket</label>
          <select id="issue-target" v-model="issueTarget">
            <option v-for="target in writableTargets" :key="target.name" :value="target.name">
              {{ target.name }}
            </option>
          </select>
          <label for="issue-scope">Dépôt</label>
          <input id="issue-scope" v-model="issueScope" type="text" maxlength="140" required />
          <label for="issue-title">Titre du ticket</label>
          <input id="issue-title" v-model="issueTitle" type="text" maxlength="256" required />
          <label for="issue-body">Texte</label>
          <textarea id="issue-body" v-model="issueBody" maxlength="8000" rows="4" />
          <button type="submit" :disabled="busy">Prévisualiser</button>
        </form>
        <div v-if="preview" class="banner banner--confirm" role="status">
          <p>
            Rien n'a été envoyé. Le ticket serait ouvert dans <code>{{ preview.scope }}</code> :
            « {{ preview.title }} ».
          </p>
          <button type="button" :disabled="busy" @click="sendIssue">Ouvrir le ticket</button>
        </div>
        <p v-if="opened" class="banner" role="status">
          Ticket ouvert et lié :
          {{ opened.reference?.external_id }}.
        </p>
      </details>
    </template>
  </section>
</template>

<style scoped>
.references {
  display: grid;
  gap: 0.8rem;
  padding: 1rem;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.references__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
}
.references__header h3 {
  margin: 0;
}
table {
  width: 100%;
  border-collapse: collapse;
}
th,
td {
  text-align: left;
  padding: 0.5rem 0.6rem;
  border-bottom: 1px solid var(--border);
}
.id {
  display: block;
  opacity: 0.7;
}
.pill {
  padding: 0.1rem 0.5rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: 0.85rem;
}
.add,
.issue form {
  display: grid;
  gap: 0.4rem;
  max-width: 32rem;
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
.hint {
  margin: 0;
  opacity: 0.75;
}
.banner {
  margin: 0;
  padding: 0.6rem 0.9rem;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.banner--error {
  border-color: #c62828;
  color: #c62828;
}
.banner--confirm {
  border-color: #ef6c00;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
</style>
