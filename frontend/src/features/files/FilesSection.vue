<script setup lang="ts">
// Les fichiers du bucket MinIO (docs/adr/0036), un dossier à la fois.
//
// Le dossier ouvert est dans l'URL (`?prefix=`) : c'est une question qu'on
// partage et qu'on remonte avec « précédent ». L'API décide qui écrit ; ici on
// cache seulement ce qu'un lecteur ne peut pas faire.
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { messageOf } from '../../lib/api'
import { useMe } from '../../lib/me'
import {
  breadcrumbs,
  detailsOf,
  noDetails,
  saveAs,
  tagsOf,
  useFiles,
  type FileDetails,
  type StoredFile,
} from './useFiles'

const route = useRoute()
const router = useRouter()
const { canWrite } = useMe()
const files = useFiles()

const failure = ref('')
const busy = ref(false)
/** La clé dont la suppression attend une confirmation. */
const confirming = ref<string | null>(null)
/** Les fichiers que l'API a refusés parce qu'ils existent déjà. */
const clashes = ref<File[]>([])
/** La clé dont la fiche est ouverte en édition, et ce qui y est écrit. */
const describing = ref<string | null>(null)
const written = ref<FileDetails>(noDetails())
/** Les tags se saisissent séparés par des virgules ; le serveur normalise. */
const writtenTags = ref('')
/** Ce que le dernier rapprochement a changé, une fois qu'il a tourné. */
const reconciled = ref<{ recorded: number; forgotten: number } | null>(null)

const prefix = computed(() => (typeof route.query.prefix === 'string' ? route.query.prefix : ''))
const crumbs = computed(() => breadcrumbs(prefix.value))

watch(
  prefix,
  (value) => {
    confirming.value = null
    clashes.value = []
    describing.value = null
    reconciled.value = null
    void files.load(value)
  },
  { immediate: true },
)

function openFolder(next: string): void {
  void router.push({ query: { ...route.query, prefix: next || undefined } })
}

async function onPicked(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const picked = [...(input.files ?? [])]
  busy.value = true
  failure.value = ''
  // Built up as files succeed or come back "exists", so a later refusal
  // (stopping the loop) still leaves the ones already found offered below.
  const refused: File[] = []
  try {
    for (const file of picked) {
      if ((await files.upload(prefix.value, file)) === 'exists') {
        refused.push(file)
      }
    }
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    clashes.value = refused
    busy.value = false
    input.value = ''
    // Whatever uploaded before a mid-batch refusal is stored server-side
    // already; the listing must say so rather than stay one request stale.
    await files.load(prefix.value)
  }
}

async function replaceClashes(): Promise<void> {
  busy.value = true
  failure.value = ''
  try {
    for (const file of clashes.value) {
      await files.upload(prefix.value, file, true)
    }
    clashes.value = []
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
    await files.load(prefix.value)
  }
}

async function onDownload(file: StoredFile): Promise<void> {
  failure.value = ''
  try {
    saveAs(await files.download(file.key), file.name)
  } catch (caught) {
    failure.value = messageOf(caught)
  }
}

async function confirmRemove(): Promise<void> {
  const key = confirming.value
  if (!key) {
    return
  }
  busy.value = true
  try {
    await files.remove(key)
    confirming.value = null
    await files.load(prefix.value)
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

function openDetails(file: StoredFile): void {
  confirming.value = null
  describing.value = file.key
  written.value = detailsOf(file)
  writtenTags.value = (written.value.tags ?? []).join(', ')
}

async function saveDetails(): Promise<void> {
  const key = describing.value
  if (!key) {
    return
  }
  busy.value = true
  failure.value = ''
  try {
    await files.describe(key, { ...written.value, tags: tagsOf(writtenTags.value) })
    describing.value = null
    await files.load(prefix.value)
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

async function onReconcile(): Promise<void> {
  busy.value = true
  failure.value = ''
  try {
    reconciled.value = await files.reconcile()
    await files.load(prefix.value)
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}

function weight(bytes: number): string {
  if (bytes < 1024) return `${bytes} o`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} ko`
  return `${(bytes / (1024 * 1024)).toFixed(1)} Mo`
}
</script>

<template>
  <section class="files" aria-label="Fichiers">
    <h2>Fichiers</h2>
    <p class="hint">
      Tout type de fichier, au plus 50 Mo. Un fichier déposé dans <code>inbox/</code> est lu par le
      pipeline qui alimente le catalogue — en texte seulement (markdown, texte brut) ; les autres
      dossiers acceptent tout type de fichier.
    </p>

    <nav class="crumbs" aria-label="Dossier courant">
      <button type="button" class="link" @click="openFolder('')">Racine</button>
      <template v-for="crumb in crumbs" :key="crumb.prefix">
        <span aria-hidden="true">/</span>
        <button type="button" class="link" @click="openFolder(crumb.prefix)">{{ crumb.label }}</button>
      </template>
    </nav>

    <p v-if="canWrite" class="upload">
      <label for="files-input">Déposer des fichiers</label>
      <input id="files-input" type="file" multiple :disabled="busy" @change="onPicked" />
      <button type="button" class="secondary" :disabled="busy" @click="onReconcile">
        Rapprocher le catalogue
      </button>
    </p>

    <p v-if="reconciled" class="banner" role="status">
      {{ reconciled.recorded }} fichier(s) ajouté(s) au catalogue,
      {{ reconciled.forgotten }} fiche(s) retirée(s).
    </p>

    <div v-if="clashes.length > 0" class="banner" role="alert">
      <p>{{ clashes.map((file) => file.name).join(', ') }} existe déjà dans ce dossier.</p>
      <button type="button" :disabled="busy" @click="replaceClashes">Remplacer</button>
      <button type="button" class="secondary" @click="clashes = []">Annuler</button>
    </div>

    <p v-if="failure" class="banner banner--error" role="alert">{{ failure }}</p>
    <p v-if="files.status.value === 'error'" class="banner banner--error" role="alert">
      {{ files.error.value }}
    </p>

    <template v-else-if="files.listing.value">
      <p
        v-if="files.listing.value.folders.length === 0 && files.listing.value.files.length === 0"
        class="hint"
      >
        Ce dossier est vide.
      </p>
      <table v-else>
        <caption class="sr-only">Contenu du dossier</caption>
        <thead>
          <tr>
            <th scope="col">Nom</th>
            <th scope="col">Description</th>
            <th scope="col">Taille</th>
            <th scope="col">Modifié</th>
            <th scope="col">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="folder in files.listing.value.folders" :key="folder">
            <td>
              <button type="button" class="link" @click="openFolder(folder)">
                {{ folder.slice(files.listing.value.prefix.length) }}
              </button>
            </td>
            <td />
            <td />
            <td />
            <td />
          </tr>
          <template v-for="file in files.listing.value.files" :key="file.key">
            <tr>
              <td>
                <span class="name">{{ file.metadata?.title || file.name }}</span>
                <span v-if="file.metadata?.title" class="hint">{{ file.name }}</span>
              </td>
              <td>
                <span v-if="file.metadata?.description">{{ file.metadata.description }}</span>
                <!-- Pas de fiche : le fichier existe, on n'en sait rien. Le
                     bucket a une autre porte que cette API — docs/adr/0039. -->
                <span v-else-if="!file.metadata" class="hint">Aucune fiche</span>
                <ul v-if="file.metadata?.tags.length" class="tags">
                  <li v-for="tag in file.metadata.tags" :key="tag">{{ tag }}</li>
                </ul>
                <span v-if="file.metadata?.uploaded_by" class="hint">
                  Déposé par {{ file.metadata.uploaded_by }}
                </span>
              </td>
              <td>{{ weight(file.size) }}</td>
              <td>{{ file.last_modified.slice(0, 10) }}</td>
              <td>
                <button type="button" class="secondary" :aria-label="`Télécharger ${file.name}`" @click="onDownload(file)">
                  Télécharger
                </button>
                <template v-if="canWrite">
                  <button
                    type="button"
                    class="secondary"
                    :aria-label="`Décrire ${file.name}`"
                    @click="openDetails(file)"
                  >
                    Décrire
                  </button>
                  <template v-if="confirming === file.key">
                    <button type="button" :disabled="busy" @click="confirmRemove">Confirmer la suppression</button>
                    <button type="button" class="secondary" @click="confirming = null">Annuler</button>
                  </template>
                  <button
                    v-else
                    type="button"
                    class="secondary"
                    :aria-label="`Supprimer ${file.name}`"
                    @click="confirming = file.key"
                  >
                    Supprimer
                  </button>
                </template>
              </td>
            </tr>
            <tr v-if="describing === file.key">
              <td colspan="5">
                <form class="details" @submit.prevent="saveDetails">
                  <p>
                    <label :for="`title-${file.key}`">Titre</label>
                    <input :id="`title-${file.key}`" v-model="written.title" maxlength="200" />
                  </p>
                  <p>
                    <label :for="`description-${file.key}`">Description</label>
                    <textarea
                      :id="`description-${file.key}`"
                      v-model="written.description"
                      maxlength="4000"
                      rows="3"
                    />
                  </p>
                  <p>
                    <label :for="`tags-${file.key}`">Étiquettes</label>
                    <input :id="`tags-${file.key}`" v-model="writtenTags" placeholder="réseau, budget" />
                  </p>
                  <p class="hint">
                    Les trois sont remplacés ensemble : ce qui est enregistré est ce qui est
                    affiché ici.
                  </p>
                  <p>
                    <button type="submit" :disabled="busy">Enregistrer</button>
                    <button type="button" class="secondary" @click="describing = null">Annuler</button>
                  </p>
                </form>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
      <p v-if="files.listing.value.truncated" class="hint">
        Seules les 1000 premières entrées de ce dossier sont affichées.
      </p>
    </template>
  </section>
</template>

<style scoped>
.files {
  display: grid;
  gap: 0.8rem;
}
.crumbs {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  align-items: center;
  margin-block: 0.5rem;
}
.upload {
  display: flex;
  align-items: center;
  gap: 0.6rem;
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
.hint {
  margin: 0;
  opacity: 0.75;
}
.name {
  display: block;
  font-weight: 600;
}
td .hint {
  display: block;
  font-size: 0.85em;
}
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  margin: 0.3rem 0;
  padding: 0;
  list-style: none;
}
.tags li {
  padding: 0.1rem 0.5rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: 0.85em;
}
.details {
  display: grid;
  gap: 0.4rem;
  max-width: 40rem;
}
.details p {
  display: grid;
  gap: 0.2rem;
  margin: 0;
}
.details input,
.details textarea {
  font: inherit;
  padding: 0.4rem 0.6rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: transparent;
  color: inherit;
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
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
</style>
