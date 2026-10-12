<script setup lang="ts">
// « Générer un rapport… » : choose a template and a format for one subject, then
// download it (everybody) or deposit it in the files (editors).
//
// The API refuses a reader's deposit anyway (docs/adr/0032); hiding the button
// only spares them a control that could not work. `downloadOnly` is for the
// reader mode, which never offers a write.
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { useMe } from '../../lib/me'
import { useReport, type ReportFormat, type SubjectKind } from './useReport'

const props = defineProps<{
  subjectKind: SubjectKind
  subjectId: string
  downloadOnly?: boolean
}>()

const router = useRouter()
const { canWrite } = useMe()
const report = useReport()

const open = ref(false)
const template = ref('')
const format = ref<ReportFormat>('html')

const options = computed(() => report.templatesFor(props.subjectKind))
const formats = computed(
  () => options.value.find((candidate) => candidate.id === template.value)?.formats ?? [],
)
const busy = computed(() => report.status.value === 'loading')
const mayDeposit = computed(() => canWrite.value && !props.downloadOnly)

watch(
  options,
  (list) => {
    if (!list.some((candidate) => candidate.id === template.value)) {
      template.value = list[0]?.id ?? ''
    }
  },
  { immediate: true },
)
watch(
  formats,
  (list) => {
    if (!list.includes(format.value)) {
      format.value = list.includes('pdf') ? 'pdf' : (list[0] ?? 'html')
    }
  },
  { immediate: true },
)

async function show(): Promise<void> {
  open.value = true
  await report.load()
}

async function download(): Promise<void> {
  await report.download(template.value, props.subjectKind, props.subjectId, format.value)
}

async function deposit(): Promise<void> {
  await report.deposit(template.value, props.subjectKind, props.subjectId, format.value)
  if (report.deposited.value) {
    await router.push({ path: '/fichiers', query: { prefix: 'reports/' } })
  }
}
</script>

<template>
  <div class="report">
    <button type="button" class="secondary" @click="show">Générer un rapport…</button>

    <form v-if="open" class="report__form" aria-label="Générer un rapport" @submit.prevent>
      <p v-if="report.catalogueError.value" class="banner banner--error" role="alert">
        {{ report.catalogueError.value }}
      </p>
      <p v-else-if="options.length === 0" class="hint">Aucun modèle de rapport pour ce sujet.</p>
      <template v-else>
        <div class="field">
          <label for="report-template">Modèle</label>
          <select id="report-template" v-model="template">
            <option v-for="candidate in options" :key="candidate.id" :value="candidate.id">
              {{ candidate.title }}
            </option>
          </select>
        </div>
        <div class="field">
          <label for="report-format">Format</label>
          <select id="report-format" v-model="format">
            <option v-for="candidate in formats" :key="candidate" :value="candidate">
              {{ candidate.toUpperCase() }}
            </option>
          </select>
        </div>
        <button type="button" :disabled="busy || !template" @click="download">Télécharger</button>
        <button v-if="mayDeposit" type="button" :disabled="busy || !template" @click="deposit">
          Déposer dans les fichiers
        </button>
      </template>
      <p v-if="report.error.value" class="banner banner--error" role="alert">
        {{ report.error.value }}
      </p>
      <button type="button" class="secondary" @click="open = false">Fermer</button>
    </form>
  </div>
</template>
