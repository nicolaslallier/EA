import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import IpamSection from '../src/features/ipam/IpamSection.vue'
import { createAppRouter } from '../src/router'
import {
  aPage,
  aRelationship,
  aSubnet,
  aSubnetDetail,
  anAddress,
  anAddressLocation,
  anElement,
  METAMODEL,
  stubApi,
  type RecordedCall,
  type Route,
} from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const HOST = anElement({ id: '11111111-1111-4111-8111-111111111111', name: 'srv-app-01' })
const BILLING = anElement({ id: 'bbbbbbbb-2222-4222-8222-222222222222', name: 'Facturation' })
const DMZ = aSubnet({ used: 1, free: 253 })

const CATALOGUE: Route = { path: '/elements', body: aPage([HOST, BILLING]) }
const PALETTE: Route = { path: '/metamodel', body: METAMODEL }
const SUBNETS: Route = { path: '/ipam/subnets', body: [DMZ] }
const DETAIL: Route = {
  path: `/ipam/subnets/${DMZ.element_id}`,
  body: aSubnetDetail(DMZ, [anAddress()], '10.0.1.1'),
}
const LOCATION: Route = {
  path: '/ipam/addresses/10.0.1.12',
  body: anAddressLocation(
    anAddress(),
    [HOST, BILLING],
    [aRelationship({ source_id: HOST.id, target_id: BILLING.id })],
  ),
}

const ROUTES = [CATALOGUE, PALETTE, SUBNETS, DETAIL, LOCATION]

async function open(query = '', routes: Route[] = ROUTES): Promise<RecordedCall[]> {
  const calls = stubApi(routes)
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/ipam${query}`)
  await router.isReady()
  render(IpamSection, { global: { plugins: [router] } })
  return calls
}

describe('IpamSection', () => {
  it('lists the declared subnets with the counts, not a percentage', async () => {
    await open()

    expect(await screen.findByText('10.0.1.0/24')).toBeInTheDocument()
    expect(screen.getByText('1 / 254')).toBeInTheDocument()
    expect(screen.getByText('253')).toBeInTheDocument()
  })

  it('says what to do first when nothing is declared', async () => {
    await open('', [CATALOGUE, PALETTE, { path: '/ipam/subnets', body: [] }])

    expect(
      await screen.findByText(/déclare-en un pour pouvoir y attribuer des adresses/i),
    ).toBeInTheDocument()
  })

  it('answers "this address, that is what?" with the element and its links', async () => {
    await open('?address=10.0.1.12')

    expect(await screen.findByRole('heading', { name: '10.0.1.12' })).toBeInTheDocument()
    expect(screen.getByText('srv-app-01')).toBeInTheDocument()
    expect(await screen.findByText(/relié à facturation/i)).toBeInTheDocument()
  })

  it('says plainly when nothing holds the address, rather than raising an alert', async () => {
    await open('?address=10.0.1.12', [
      CATALOGUE,
      PALETTE,
      SUBNETS,
      { path: '/ipam/addresses/10.0.1.12', status: 404, body: { error: 'not_found', detail: 'x' } },
    ])

    expect(await screen.findByText(/rien ne répond sur « 10.0.1.12 »/i)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('puts the address looked up in the URL, so the view can be sent to a colleague', async () => {
    await open()

    await fireEvent.update(await screen.findByLabelText(/quelle adresse/i), '10.0.1.12')
    await fireEvent.click(screen.getByRole('button', { name: 'Chercher' }))

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: '10.0.1.12' })).toBeInTheDocument(),
    )
  })

  it('opens a subnet from its name and shows what is inside it', async () => {
    await open()

    await fireEvent.click(await screen.findByRole('button', { name: 'DMZ' }))

    expect(await screen.findByRole('heading', { name: /DMZ — 10\.0\.1\.0\/24/ })).toBeInTheDocument()
    expect(screen.getByText('10.0.1.12')).toBeInTheDocument()
  })

  it('offers the next free address by name, so nobody has to compute it', async () => {
    await open(`?subnet=${DMZ.element_id}`)

    expect(await screen.findByRole('button', { name: /attribuer 10\.0\.1\.1/i })).toBeInTheDocument()
  })

  it('asks the server for the next address rather than sending one it chose', async () => {
    const calls = await open(`?subnet=${DMZ.element_id}`, [
      ...ROUTES,
      { method: 'POST', path: `/ipam/subnets/${DMZ.element_id}/allocate`, status: 201, body: anAddress() },
    ])

    await fireEvent.update(await screen.findByLabelText(/attribuer la prochaine adresse à/i), HOST.id)
    await fireEvent.click(screen.getByRole('button', { name: /attribuer/i }))

    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.method === 'POST' &&
            call.url.pathname === `/ipam/subnets/${DMZ.element_id}/allocate` &&
            (call.body as { element_id: string }).element_id === HOST.id,
        ),
      ).toBe(true),
    )
  })

  it('refuses to offer an allocation from a subnet that is full', async () => {
    await open(`?subnet=${DMZ.element_id}`, [
      CATALOGUE,
      PALETTE,
      SUBNETS,
      {
        path: `/ipam/subnets/${DMZ.element_id}`,
        body: aSubnetDetail(aSubnet({ used: 254, free: 0 }), [anAddress()], null),
      },
    ])

    expect(await screen.findByText(/ce sous-réseau est plein/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /attribuer/i })).toBeDisabled()
  })

  it("shows the server's refusal instead of swallowing it", async () => {
    await open(`?subnet=${DMZ.element_id}`, [
      ...ROUTES,
      {
        method: 'POST',
        path: '/ipam/subnets',
        status: 409,
        body: { error: 'duplicate', detail: '10.0.1.0/24 est déjà déclaré' },
      },
    ])

    await fireEvent.update(await screen.findByLabelText('Nom'), 'DMZ bis')
    await fireEvent.update(screen.getByLabelText('Préfixe'), '10.0.1.0/24')
    await fireEvent.click(screen.getByRole('button', { name: 'Déclarer' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('10.0.1.0/24 est déjà déclaré')
  })

  it('releases an address through the element that held it', async () => {
    const calls = await open(`?subnet=${DMZ.element_id}`, [
      ...ROUTES,
      { method: 'DELETE', path: `/ipam/elements/${HOST.id}/address`, status: 204 },
    ])

    await fireEvent.click(await screen.findByRole('button', { name: 'Libérer' }))

    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.method === 'DELETE' && call.url.pathname === `/ipam/elements/${HOST.id}/address`,
        ),
      ).toBe(true),
    )
  })
})
