<script setup lang="ts">
// The `/analyse/qualite` section: what is wrong with the model, by rule.
//
// Every finding is computed by the API on request; this screen only reads and
// translates them. The filters live in the URL (`?severity=`, `?code=`) so a
// view of "all orphans" is a link one can send. A finding's message carries
// element names — user data — and is rendered as text by Vue, never as HTML.
import { computed, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { RULE_LABELS, SEVERITY_LABELS, type RuleCode, type Severity } from './labels'
import { useQuality, type FindingRead, type QualityFilters } from './useQuality'

const route = useRoute()
const router = useRouter()
const quality = useQuality()

function one(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

// A value typed by hand is passed on as it stands: the API knows the codes and
// refuses anything else, and its refusal is the message the user should read.
const filters = computed<QualityFilters>(() => ({
  severity: one(route.query.severity) as Severity | '',
  code: one(route.query.code) as RuleCode | '',
}))

watch(
  filters,
  async (current) => {
    await quality.load(current)
  },
  { immediate: true },
)

function ask(changes: Record<string, string | undefined>): void {
  void router.replace({ query: { ...route.query, ...changes } })
}

const groups = computed(() => {
  const byRule = new Map<RuleCode, FindingRead[]>()
  for (const finding of quality.findings.value) {
    byRule.set(finding.rule, [...(byRule.get(finding.rule) ?? []), finding])
  }
  return [...byRule.entries()].map(([rule, items]) => ({ rule, items }))
})
</script>

<template>
  <section class="section">
    <header>
      <h2>Qualité du modèle</h2>
      <p class="hint">Orphelins, doublons, nommage, données manquantes, liens devenus incohérents.</p>
    </header>

    <p v-if="quality.status.value === 'error'" class="banner banner--error" role="alert">
      {{ quality.error.value }}
    </p>

    <template v-if="quality.report.value">
      <div class="counts" role="group" aria-label="Constats par règle">
        <button
          v-for="count in quality.report.value.counts"
          :key="count.rule"
          type="button"
          :aria-pressed="filters.code === count.rule"
          @click="ask({ code: filters.code === count.rule ? undefined : count.rule })"
        >
          {{ RULE_LABELS[count.rule] }} ({{ count.count }})
        </button>
      </div>

      <div class="controls">
        <div class="field">
          <label for="quality-severity">Gravité</label>
          <select
            id="quality-severity"
            :value="filters.severity"
            @change="ask({ severity: ($event.target as HTMLSelectElement).value || undefined })"
          >
            <option value="">Toutes</option>
            <option v-for="(label, value) in SEVERITY_LABELS" :key="value" :value="value">
              {{ label }}
            </option>
          </select>
        </div>
      </div>

      <p v-if="quality.findings.value.length === 0" class="hint">
        Aucun constat pour ce filtre — {{ quality.report.value.total }} sur l'ensemble du modèle.
      </p>

      <table v-else class="table">
        <thead>
          <tr>
            <th>Règle</th>
            <th>Gravité</th>
            <th>Élément</th>
            <th>Constat</th>
            <th><span class="sr-only">Action</span></th>
          </tr>
        </thead>
        <tbody v-for="group in groups" :key="group.rule">
          <tr v-for="finding in group.items" :key="`${finding.rule}-${finding.element_id}-${finding.related_id ?? ''}-${finding.message}`">
            <td>{{ RULE_LABELS[finding.rule] }}</td>
            <td>{{ SEVERITY_LABELS[finding.severity] }}</td>
            <td>{{ finding.element_name }}</td>
            <td>{{ finding.message }}</td>
            <td>
              <RouterLink :to="finding.fix_hint">Corriger</RouterLink>
            </td>
          </tr>
        </tbody>
      </table>

      <button
        v-if="quality.report.value.next_cursor !== null"
        type="button"
        @click="void quality.more(filters)"
      >
        Afficher la suite
      </button>
    </template>
    <p v-else-if="quality.status.value === 'loading'" class="hint">Analyse en cours…</p>
  </section>
</template>
