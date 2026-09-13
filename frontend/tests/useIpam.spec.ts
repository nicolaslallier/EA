import { afterEach, describe, expect, it, vi } from 'vitest'

import { useIpam } from '../src/features/ipam/useIpam'
import {
  aSubnet,
  aSubnetDetail,
  anAddress,
  anAddressLocation,
  deferApi,
  stubApi,
} from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const DMZ = aSubnet()
const LAN = aSubnet({ element_id: 'dddddddd-2222-4222-8222-222222222222', name: 'LAN', cidr: '10.0.2.0/24' })
const DOWN = { error: 'internal', detail: 'Le graphe est tombé.' }

describe('useIpam', () => {
  it('takes the banner down once a subnet opens after one that failed', async () => {
    stubApi([{ path: `/ipam/subnets/${DMZ.element_id}`, status: 500, body: DOWN }])
    const ipam = useIpam()
    await ipam.openSubnet(DMZ.element_id)
    expect(ipam.error.value).toBe('Le graphe est tombé.')

    stubApi([{ path: `/ipam/subnets/${DMZ.element_id}`, body: aSubnetDetail(DMZ) }])
    await ipam.openSubnet(DMZ.element_id)

    expect(ipam.error.value).toBe('')
    expect(ipam.detail.value?.subnet.name).toBe('DMZ')
  })

  it('takes the banner down once a lookup succeeds after one that failed', async () => {
    stubApi([{ path: '/ipam/addresses/10.0.1.12', status: 500, body: DOWN }])
    const ipam = useIpam()
    await ipam.locate('10.0.1.12')
    expect(ipam.error.value).toBe('Le graphe est tombé.')

    stubApi([{ path: '/ipam/addresses/10.0.1.12', body: anAddressLocation() }])
    await ipam.locate('10.0.1.12')

    expect(ipam.error.value).toBe('')
    expect(ipam.located.value?.address.address).toBe('10.0.1.12')
  })

  it('takes the banner down when the failed question is withdrawn', async () => {
    stubApi([{ path: '/ipam/addresses/10.0.1.12', status: 500, body: DOWN }])
    const ipam = useIpam()
    await ipam.locate('10.0.1.12')

    await ipam.locate('')

    expect(ipam.error.value).toBe('')
  })

  it('keeps a failed listing on the banner while another question succeeds', async () => {
    stubApi([
      { path: '/ipam/subnets', status: 500, body: DOWN },
      { path: `/ipam/subnets/${DMZ.element_id}`, body: aSubnetDetail(DMZ) },
    ])
    const ipam = useIpam()
    await ipam.loadSubnets()

    await ipam.openSubnet(DMZ.element_id)

    expect(ipam.error.value).toBe('Le graphe est tombé.')
  })

  it('opens the subnet clicked last, whichever answer arrives first', async () => {
    const calls = deferApi()
    const ipam = useIpam()

    const first = ipam.openSubnet(DMZ.element_id)
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    const second = ipam.openSubnet(LAN.element_id)
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    calls[1].answer(aSubnetDetail(LAN))
    calls[0].answer(aSubnetDetail(DMZ))
    await Promise.all([first, second])

    expect(ipam.detail.value?.subnet.name).toBe('LAN')
    expect(ipam.error.value).toBe('')
  })

  it('closes the subnet for good, even if its answer is still on the way', async () => {
    const calls = deferApi()
    const ipam = useIpam()

    const pending = ipam.openSubnet(DMZ.element_id)
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    await ipam.openSubnet('')
    calls[0].answer(aSubnetDetail(DMZ))
    await pending

    expect(ipam.detail.value).toBeNull()
  })

  it('answers for the address typed last, whichever answer arrives first', async () => {
    const calls = deferApi()
    const ipam = useIpam()

    const first = ipam.locate('10.0.1.12')
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    const second = ipam.locate('10.0.1.13')
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    calls[1].answer(anAddressLocation(anAddress({ address: '10.0.1.13' })))
    calls[0].answer(anAddressLocation())
    await Promise.all([first, second])

    expect(ipam.located.value?.address.address).toBe('10.0.1.13')
  })
})
