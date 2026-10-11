<script setup lang="ts">
// Profiles: the stereotypes people lay on elements, and the properties each expects.
//
// Reference data, rarely changed, read by people: the screen lists it, edits
// it for an editor, and moves it between environments as a versioned JSON file.
// It restates no rule — a refusal is the API's own sentence.
import { onMounted, ref } from 'vue'

import { messageOf } from '../../lib/api'
import { download } from '../../lib/diagramExport'
import { useMe } from '../../lib/me'
import { useMetamodel } from '../metamodel/useMetamodel'
import StereotypeEditor from './StereotypeEditor.vue'
import { useProfiles, type ProfileRead, type StereotypeRead, type StereotypeWrite } from './useProfiles'

const { canWrite } = useMe()
const profiles = useProfiles()
const metamodel = useMetamodel()

const newName = ref('')
const failure = ref('')
const editing = ref<{ profile: ProfileRead; stereotype: StereotypeRead | null } | null>(null)

onMounted(async () => {
  await Promise.all([profiles.load(), metamodel.load()])
})

/** Every base type the metamodel offers, labelled — asked for, never listed here. */
function types(): { value: string; label: string }[] {
  return metamodel.elementTypes.value.map((type) => ({ value: type.value, label: type.label }))
}

async function attempt(action: () => Promise<void>): Promise<void> {
  failure.value = ''
  try {
    await action()
  } catch (caught) {
    failure.value = messageOf(caught)
  }
}

async function create(): Promise<void> {
  await attempt(async () => {
    await profiles.createProfile(newName.value.trim())
    newName.value = ''
  })
}

async function save(body: StereotypeWrite): Promise<void> {
  const target = editing.value
  if (!target) {
    return
  }
  await attempt(async () => {
    await profiles.saveStereotype(target.profile.id, target.stereotype?.id ?? null, body)
    editing.value = null
  })
}

async function exportFile(profile: ProfileRead): Promise<void> {
  await attempt(async () => {
    const text = await profiles.exportProfile(profile.id)
    download(new Blob([text], { type: 'application/json' }), `${profile.name}.profil.json`)
  })
}

async function importFile(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) {
    return
  }
  await attempt(async () => profiles.importProfile(await file.text()))
  input.value = ''
}
</script>

<template>
  <section class="profiles">
    <header>
      <h2>Profils</h2>
      <p class="hint">
        Un stéréotype est une étiquette posée sur un élément d'un type donné, avec les propriétés
        attendues. Il ne change ni le type de l'élément, ni ce qu'il peut relier.
      </p>
    </header>

    <form v-if="canWrite" class="create" aria-label="Créer un profil" @submit.prevent="create">
      <label for="profile-name">Nom du profil</label>
      <input id="profile-name" v-model="newName" maxlength="100" autocomplete="off" />
      <button type="submit" :disabled="!newName.trim()">Créer le profil</button>
      <label for="profile-import">Importer un profil</label>
      <input id="profile-import" type="file" accept="application/json,.json" @change="importFile" />
    </form>

    <p v-if="failure" class="banner banner--error" role="alert">{{ failure }}</p>
    <p v-if="profiles.status.value === 'error'" class="banner banner--error" role="alert">
      {{ profiles.error.value }}
    </p>

    <article v-for="profile in profiles.profiles.value" :key="profile.id" class="profile">
      <header>
        <h3>{{ profile.name }}</h3>
        <button type="button" class="secondary" :aria-label="`Exporter ${profile.name}`" @click="exportFile(profile)">
          Exporter
        </button>
        <button
          v-if="canWrite"
          type="button"
          class="secondary"
          :aria-label="`Supprimer le profil ${profile.name}`"
          @click="attempt(() => profiles.removeProfile(profile.id))"
        >
          Supprimer
        </button>
      </header>
      <p v-if="profile.description" class="hint">{{ profile.description }}</p>

      <ul class="stereotypes">
        <li
          v-for="stereotype in profile.stereotypes"
          :key="stereotype.id"
          :aria-label="stereotype.name"
        >
          <strong>«{{ stereotype.name }}»</strong> sur {{ metamodel.labelOf(stereotype.base_type) }}
          <ul>
            <li v-for="prop in stereotype.properties" :key="prop.key">
              {{ prop.label }} (<code>{{ prop.key }}</code>)<span v-if="prop.required"> — obligatoire</span>
              <span v-if="prop.kind === 'enum'"> — {{ prop.allowed_values.join(', ') }}</span>
              <span v-if="prop.kind === 'template'"> — gabarit <code>{{ prop.template }}</code></span>
            </li>
          </ul>
          <template v-if="canWrite">
            <button type="button" class="secondary" @click="editing = { profile, stereotype }">Modifier</button>
            <button
              type="button"
              class="secondary"
              :aria-label="`Supprimer le stéréotype ${stereotype.name}`"
              @click="attempt(() => profiles.removeStereotype(stereotype.id))"
            >
              Supprimer
            </button>
          </template>
        </li>
      </ul>
      <button
        v-if="canWrite"
        type="button"
        :aria-label="`Ajouter un stéréotype à ${profile.name}`"
        @click="editing = { profile, stereotype: null }"
      >
        Ajouter un stéréotype
      </button>
      <StereotypeEditor
        v-if="editing && editing.profile.id === profile.id"
        :key="editing.stereotype?.id ?? 'new'"
        :stereotype="editing.stereotype ?? undefined"
        :types="types()"
        :failure="failure"
        @save="save"
        @cancel="editing = null"
      />
    </article>
  </section>
</template>

<style scoped>
.profiles,
.profile {
  display: grid;
  gap: 0.8rem;
}
.profile {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1rem;
}
.profile header {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
}
.profile h3 {
  margin: 0;
  flex: 1;
}
.create {
  display: grid;
  gap: 0.4rem;
  max-width: 28rem;
}
</style>
