<script setup lang="ts">
// The markdown attached to one element: what is there, adding one, reading one.
//
// It owns its own reads, like the relations panel next door: the catalogue
// screen decides *which* element is on screen, and this decides everything
// about that element's files.
//
// The markdown is shown as the text it is. Rendering it would mean a parser, a
// sanitiser and an ADR for both — see docs/adr/0017 — and a runbook read as
// plain text is still a runbook read.
import { computed, onMounted, ref } from 'vue'

import { messageOf } from '../../lib/api'
import type { ElementRead } from '../elements/useElementCatalogue'
import { useElementDocuments } from './useElementDocuments'

const props = defineProps<{ element: ElementRead }>()
defineEmits<{ close: [] }>()

const documents = useElementDocuments()
const failure = ref('')
const busy = ref(false)
/** The document whose deletion is waiting for a confirmation. */
const confirming = ref<string | null>(null)

onMounted(async () => {
  await documents.load(props.element.id)
})

async function onPicked(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) {
    return
  }
  busy.value = true
  failure.value = ''
  try {
    await documents.upload(props.element.id, file)
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
    // Clearing the input matters: picking the same file twice in a row fires
    // no `change` event otherwise, so a corrected file would look ignored.
    input.value = ''
  }
}

// A read reports its own failure (`documents.readError`) rather than throwing:
// two names clicked in a row are two reads, and only the last one may speak.
async function onOpen(documentId: string): Promise<void> {
  failure.value = ''
  await documents.open(documentId)
}

/** What the banner says: a refused write first, else why a document could not be read. */
const shownFailure = computed(() => failure.value || documents.readError.value)

async function confirmRemove(): Promise<void> {
  const documentId = confirming.value
  if (!documentId) {
    return
  }
  busy.value = true
  try {
    await documents.remove(props.element.id, documentId)
    confirming.value = null
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

/** Bytes are for machines; a size is what the reader needs. */
function weight(bytes: number): string {
  return bytes < 1024 ? `${bytes} o` : `${Math.round(bytes / 1024)} ko`
}

function day(iso: string): string {
  return iso.slice(0, 10)
}
</script>

<template>
  <section class="documents" aria-label="Documents de l'élément">
    <header class="documents__header">
      <h3>Documents de « {{ element.name }} »</h3>
      <button type="button" class="secondary" @click="$emit('close')">Fermer</button>
    </header>

    <p class="hint">
      Des fichiers markdown (<code>.md</code>), au plus 1 Mo, conservés tels quels.
      Réenvoyer un fichier déjà présent en remplace le contenu.
    </p>

    <p class="upload">
      <label class="upload__label" for="document-file">Ajouter un fichier</label>
      <input
        id="document-file"
        type="file"
        accept=".md,.markdown,text/markdown"
        :disabled="busy"
        @change="onPicked"
      />
    </p>

    <p v-if="shownFailure" class="banner banner--error" role="alert">{{ shownFailure }}</p>

    <p v-if="documents.status.value === 'error'" class="banner banner--error" role="alert">
      {{ documents.error.value }}
    </p>

    <p v-else-if="documents.items.value.length === 0" class="hint">
      Aucun document attaché pour l'instant.
    </p>

    <table v-else>
      <caption class="sr-only">Documents attachés à l'élément</caption>
      <thead>
        <tr>
          <th scope="col">Fichier</th>
          <th scope="col">Taille</th>
          <th scope="col">Modifié</th>
          <th scope="col">Actions</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="document in documents.items.value" :key="document.id">
          <td>
            <button type="button" class="link" @click="onOpen(document.id)">
              {{ document.filename }}
            </button>
          </td>
          <td>{{ weight(document.byte_size) }}</td>
          <td>{{ day(document.updated_at) }}</td>
          <td>
            <button
              type="button"
              class="secondary"
              :aria-label="`Supprimer ${document.filename}`"
              @click="confirming = document.id"
            >
              Supprimer
            </button>
          </td>
        </tr>
      </tbody>
    </table>

    <p v-if="confirming" class="banner banner--confirm" role="alert">
      Supprimer ce document ?
      <button type="button" :disabled="busy" @click="confirmRemove">Confirmer</button>
      <button type="button" class="secondary" @click="confirming = null">Renoncer</button>
    </p>

    <section
      v-if="documents.opened.value"
      class="reader"
      aria-label="Contenu du document"
    >
      <header class="reader__header">
        <h4>{{ documents.opened.value.filename }}</h4>
        <button type="button" class="secondary" @click="documents.close()">Fermer</button>
      </header>
      <pre>{{ documents.opened.value.content }}</pre>
    </section>
  </section>
</template>

<style scoped>
.documents {
  display: grid;
  gap: 0.8rem;
  padding: 1rem;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.documents__header,
.reader__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
}
.documents__header h3,
.reader__header h4 {
  margin: 0;
}
.upload {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin: 0;
}
.upload__label {
  font-size: 0.85rem;
  font-weight: 600;
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
/* The file name is a real control — focusable and announced as a button —
   drawn as the link it behaves like, exactly as in the catalogue. */
.link {
  padding: 0;
  border: none;
  background: none;
  color: inherit;
  font: inherit;
  font-weight: 600;
  text-align: left;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.reader {
  border-top: 1px solid var(--border);
  padding-top: 0.8rem;
}
.reader pre {
  margin: 0.4rem 0 0;
  padding: 0.8rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow-x: auto;
  white-space: pre-wrap;
  font-size: 0.9rem;
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
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
</style>
