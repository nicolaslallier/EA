<script setup lang="ts">
// The `/ipam` section: the subnets, what is in them, and what an address is.
//
// There is nothing here the catalogue does not already hold: a subnet is a
// `communication_network` element and an address is one of its occupant's
// attributes (docs/adr/0020). This screen is a *reading* of that, arranged
// around the two questions a spreadsheet answers badly — "which address is
// free" and "10.0.1.12, that is what?".
//
// Which subnet is open, which scope is shown and which address was looked up
// all live in the URL, per the rule in CLAUDE.md: those are questions somebody
// would send to a colleague. The declaration form is not, and stays in a ref.
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useElementCatalogue } from '../elements/useElementCatalogue'
import { useMetamodel } from '../metamodel/useMetamodel'
import { DEFAULT_VRF, useIpam } from './useIpam'

const route = useRoute()
const router = useRouter()
const ipam = useIpam()
const catalogue = useElementCatalogue()
const metamodel = useMetamodel()

/** A query parameter is `string | string[] | null`; only one value means anything. */
function one(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

const openSubnetId = computed(() => one(route.query.subnet))
const lookedUp = computed(() => one(route.query.address))
const scope = computed(() => one(route.query.vrf))

/** What was typed in the box, before it becomes a question in the URL. */
const typed = ref('')

const declaration = reactive({ name: '', cidr: '', vrf: DEFAULT_VRF, reserved: '' })
/** Which element the open subnet's next address would go to. */
const recipient = ref('')

const occupancy = (subnet: { used: number; capacity: number; reserved: number }) =>
  subnet.capacity === 0 ? 0 : Math.round((100 * (subnet.used + subnet.reserved)) / subnet.capacity)

onMounted(async () => {
  await Promise.all([metamodel.load(), catalogue.load()])
})

watch(scope, () => ipam.loadSubnets(scope.value), { immediate: true })
watch(openSubnetId, (id) => ipam.openSubnet(id), { immediate: true })
watch(
  lookedUp,
  (address) => {
    typed.value = address
    return ipam.locate(address, scope.value || DEFAULT_VRF)
  },
  { immediate: true },
)

/**
 * Ask a new question by changing the URL.
 *
 * Opening a subnet or looking an address up is a step of an exploration, so it
 * is pushed and the back button undoes it; changing the scope filter only
 * replaces, exactly as the other sections treat a dial.
 */
function ask(changes: Record<string, string | undefined>, step = false): void {
  const query = { ...route.query, ...changes }
  void (step ? router.push({ query }) : router.replace({ query }))
}

async function onDeclare(): Promise<void> {
  const declared = await ipam.declareSubnet({ ...declaration })
  if (declared) {
    declaration.name = ''
    declaration.cidr = ''
    declaration.reserved = ''
  }
}

async function onAllocate(): Promise<void> {
  if (!openSubnetId.value || !recipient.value) {
    return
  }
  const given = await ipam.allocate(openSubnetId.value, recipient.value)
  if (given) {
    recipient.value = ''
  }
}
</script>

<template>
  <section class="ipam">
    <header>
      <h2>Adressage IP</h2>
      <p class="hint">
        Les sous-réseaux, ce qu'ils contiennent, et ce qui répond sur une adresse.
      </p>
    </header>

    <p v-if="ipam.error.value" class="banner banner--error" role="alert">
      {{ ipam.error.value }}
    </p>

    <!-- La question pour laquelle tout ceci existe. -->
    <form class="controls" role="search" aria-label="Chercher une adresse"
          @submit.prevent="ask({ address: typed || undefined }, true)">
      <div class="field">
        <label for="ipam-lookup">Quelle adresse ?</label>
        <input id="ipam-lookup" v-model="typed" type="search" placeholder="10.0.1.12" />
      </div>
      <div v-if="ipam.scopes.value.length > 1" class="field">
        <label for="ipam-scope">Portée</label>
        <select id="ipam-scope" :value="scope"
                @change="ask({ vrf: ($event.target as HTMLSelectElement).value || undefined })">
          <option value="">Toutes les portées</option>
          <option v-for="vrf in ipam.scopes.value" :key="vrf" :value="vrf">{{ vrf }}</option>
        </select>
      </div>
      <button type="submit">Chercher</button>
    </form>

    <div v-if="lookedUp && ipam.located.value" class="card">
      <h3>{{ ipam.located.value.address.address }}</h3>
      <p>
        répond sur
        <strong>{{ ipam.located.value.address.element_name }}</strong>
        <span class="type">
          ({{ metamodel.labelOf(ipam.located.value.address.element_type) }})</span>
      </p>
      <p v-if="ipam.located.value.graph.relationships.length > 0" class="hint">
        Relié à
        {{
          ipam.located.value.graph.elements
            .filter((element) => element.id !== ipam.located.value?.address.element_id)
            .map((element) => element.name)
            .join(', ')
        }}.
      </p>
      <p v-else class="hint">Cet élément n'est relié à rien pour l'instant.</p>
    </div>
    <p v-else-if="lookedUp" class="hint">
      Rien ne répond sur « {{ lookedUp }} » dans la portée
      « {{ scope || DEFAULT_VRF }} ».
    </p>

    <h3>Sous-réseaux</h3>
    <p v-if="ipam.status.value === 'loading'" class="hint">Chargement des sous-réseaux…</p>
    <p v-else-if="ipam.subnets.value.length === 0" class="hint">
      Aucun sous-réseau déclaré. Déclare-en un pour pouvoir y attribuer des adresses.
    </p>
    <table v-else>
      <caption class="sr-only">Les sous-réseaux déclarés</caption>
      <thead>
        <tr>
          <th scope="col">Nom</th>
          <th scope="col">Préfixe</th>
          <th scope="col">Portée</th>
          <th scope="col">Occupation</th>
          <th scope="col">Libres</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="subnet in ipam.subnets.value" :key="subnet.element_id">
          <td>
            <button type="button" class="link" @click="ask({ subnet: subnet.element_id }, true)">
              {{ subnet.name }}
            </button>
          </td>
          <td><code>{{ subnet.cidr }}</code></td>
          <td>{{ subnet.vrf }}</td>
          <td>
            <span class="gauge" :style="{ '--fill': `${occupancy(subnet)}%` }" />
            {{ subnet.used }} / {{ subnet.capacity }}
          </td>
          <td>{{ subnet.free }}</td>
        </tr>
      </tbody>
    </table>

    <details>
      <summary>Déclarer un sous-réseau</summary>
      <form class="controls" @submit.prevent="onDeclare">
        <div class="field">
          <label for="ipam-name">Nom</label>
          <input id="ipam-name" v-model="declaration.name" required />
        </div>
        <div class="field">
          <label for="ipam-cidr">Préfixe</label>
          <input id="ipam-cidr" v-model="declaration.cidr" required placeholder="10.0.1.0/24" />
        </div>
        <div class="field">
          <label for="ipam-vrf">Portée</label>
          <input id="ipam-vrf" v-model="declaration.vrf" required />
        </div>
        <div class="field">
          <label for="ipam-reserved">Réservées</label>
          <input id="ipam-reserved" v-model="declaration.reserved"
                 placeholder="10.0.1.1, 10.0.1.200-10.0.1.254" />
        </div>
        <button type="submit">Déclarer</button>
      </form>
    </details>

    <p v-if="ipam.refusal.value" class="banner banner--error" role="alert">
      {{ ipam.refusal.value }}
    </p>

    <template v-if="ipam.detail.value">
      <h3>{{ ipam.detail.value.subnet.name }} — {{ ipam.detail.value.subnet.cidr }}</h3>

      <form class="controls" @submit.prevent="onAllocate">
        <div class="field">
          <label for="ipam-recipient">Attribuer la prochaine adresse à</label>
          <select id="ipam-recipient" v-model="recipient">
            <option value="">Choisis un élément…</option>
            <option v-for="element in catalogue.items.value" :key="element.id" :value="element.id">
              {{ element.name }}
            </option>
          </select>
        </div>
        <button type="submit" :disabled="!recipient || ipam.detail.value.next_free === null">
          Attribuer
          <template v-if="ipam.detail.value.next_free">
            {{ ipam.detail.value.next_free }}
          </template>
        </button>
      </form>

      <p v-if="ipam.detail.value.next_free === null" class="hint">
        Ce sous-réseau est plein : plus une seule adresse libre.
      </p>

      <p v-if="ipam.detail.value.addresses.length === 0" class="hint">
        Aucune adresse attribuée dans ce sous-réseau.
      </p>
      <table v-else>
        <caption class="sr-only">Les adresses attribuées dans ce sous-réseau</caption>
        <thead>
          <tr>
            <th scope="col">Adresse</th>
            <th scope="col">Élément</th>
            <th scope="col">Type</th>
            <th scope="col"><span class="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="entry in ipam.detail.value.addresses" :key="entry.address">
            <td><code>{{ entry.address }}</code></td>
            <td>{{ entry.element_name }}</td>
            <td>{{ metamodel.labelOf(entry.element_type) }}</td>
            <td>
              <button type="button" @click="ipam.release(entry.element_id)">
                Libérer
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </template>
  </section>
</template>

<style scoped>
.ipam {
  display: grid;
  gap: 1rem;
}
header h2,
h3 {
  margin: 0 0 0.2rem;
}
.controls {
  display: flex;
  flex-wrap: wrap;
  align-items: end;
  gap: 0.6rem;
}
.field {
  display: grid;
  gap: 0.25rem;
}
label {
  font-size: 0.85rem;
  font-weight: 600;
}
input,
select {
  font: inherit;
  padding: 0.4rem 0.6rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: inherit;
  color: inherit;
}
button {
  font: inherit;
  padding: 0.4rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: inherit;
  color: inherit;
  cursor: pointer;
}
button[disabled] {
  cursor: not-allowed;
  opacity: 0.5;
}
.link {
  padding: 0;
  border: 0;
  background: none;
  color: inherit;
  text-decoration: underline;
}
table {
  width: 100%;
  border-collapse: collapse;
}
th,
td {
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid var(--border);
  text-align: left;
}
code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
/* A bar rather than a number alone: "nearly full" is read at a glance, and the
   counts beside it are what somebody actually acts on. */
.gauge {
  display: inline-block;
  width: 5rem;
  height: 0.5rem;
  margin-right: 0.5rem;
  border: 1px solid var(--border);
  border-radius: 3px;
  background: linear-gradient(
    to right,
    currentColor var(--fill),
    transparent var(--fill)
  );
  vertical-align: middle;
}
.card {
  padding: 0.8rem 1rem;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.card p {
  margin: 0.3rem 0 0;
}
.type {
  opacity: 0.75;
}
.hint {
  margin: 0;
  opacity: 0.75;
}
.banner {
  margin: 0;
  padding: 0.6rem 0.9rem;
  border: 1px solid #c62828;
  border-radius: 6px;
  color: #c62828;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
}
</style>
