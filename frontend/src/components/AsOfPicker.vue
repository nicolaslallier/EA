<script setup lang="ts">
// "And in 2028?" is a question every view has, so the date is a control of the
// shell and not of a screen. A native date input: no picker library.
import { useAsOf } from '../lib/asof'

const { asOf, setAsOf } = useAsOf()

function onChange(event: Event): void {
  setAsOf((event.target as HTMLInputElement).value)
}
</script>

<template>
  <div class="asof">
    <label for="asof-day">Vue au</label>
    <input id="asof-day" type="date" :value="asOf ?? ''" @input="onChange" />
    <button v-if="asOf" type="button" class="asof__today" @click="setAsOf('')">
      Aujourd'hui
    </button>
  </div>
</template>

<style scoped>
.asof {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.9rem;
}
.asof input {
  font: inherit;
  padding: 0.25rem 0.45rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: inherit;
  color: inherit;
}
.asof__today {
  font: inherit;
  color: inherit;
  background: none;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.25rem 0.6rem;
  cursor: pointer;
}
</style>
