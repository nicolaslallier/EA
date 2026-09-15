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
import { breadcrumbs, saveAs, useFiles, type StoredFile } from './useFiles'

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

const prefix = computed(() => (typeof route.query.prefix === 'string' ? route.query.prefix : ''))
const crumbs = computed(() => breadcrumbs(prefix.value))

watch(
  prefix,
  (value) => {
    confirming.value = null
    clashes.value = []
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
  try {
    const refused: File[] = []
    for (const file of picked) {
      if ((await files.upload(prefix.value, file)) === 'exists') {
        refused.push(file)
      }
    }
    clashes.value = refused
    await files.load(prefix.value)
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
    input.value = ''
  }
}

async function replaceClashes(): Promise<void> {
  busy.value = true
  try {
    for (const file of clashes.value) {
      await files.upload(prefix.value, file, true)
    }
    clashes.value = []
    await files.load(prefix.value)
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
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
      pipeline qui alimente le catalogue.
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
            <th scope="col">Taille</th>
            <th scope="col">Modifié</th>
            <th scope="col">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="folder in files.listing.value.folders" :key="folder">
            <td>
              <button type="button" class="link" @click="openFolder(folder)">
                {{ folder.slice(prefix.length) }}
              </button>
            </td>
            <td />
            <td />
            <td />
          </tr>
          <tr v-for="file in files.listing.value.files" :key="file.key">
            <td>{{ file.name }}</td>
            <td>{{ weight(file.size) }}</td>
            <td>{{ file.last_modified.slice(0, 10) }}</td>
            <td>
              <button type="button" class="secondary" :aria-label="`Télécharger ${file.name}`" @click="onDownload(file)">
                Télécharger
              </button>
              <template v-if="canWrite">
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
