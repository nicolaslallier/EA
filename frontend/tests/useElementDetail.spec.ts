import { afterEach, describe, expect, it, vi } from 'vitest'

import { useElementDetail } from '../src/features/elements/useElementDetail'
import { anElement, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const ELEMENT = anElement({ name: 'Facturation' })

describe('useElementDetail', () => {
  it('asks the API for the element behind an id', async () => {
    const calls = stubApi([{ path: `/elements/${ELEMENT.id}`, body: ELEMENT }])
    const detail = useElementDetail()

    await detail.open(ELEMENT.id)

    expect(calls.at(-1)?.url.pathname).toBe(`/elements/${ELEMENT.id}`)
    expect(detail.element.value?.name).toBe('Facturation')
    expect(detail.status.value).toBe('ready')
  })

  it('reports an element the graph no longer holds', async () => {
    stubApi([
      {
        path: `/elements/${ELEMENT.id}`,
        status: 404,
        body: { error: 'not_found', detail: "Cet élément n'existe pas" },
      },
    ])
    const detail = useElementDetail()

    await detail.open(ELEMENT.id)

    expect(detail.status.value).toBe('error')
    expect(detail.error.value).toMatch(/n'existe pas/i)
    expect(detail.element.value).toBeNull()
  })

  it('reports an unreachable backend', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )
    const detail = useElementDetail()

    await detail.open(ELEMENT.id)

    expect(detail.error.value).toMatch(/injoignable/i)
  })

  it('shows nothing of the previous element while the next one loads', async () => {
    const other = anElement({ id: '22222222-2222-4222-8222-222222222222', name: 'Grand livre' })
    stubApi([
      { path: `/elements/${ELEMENT.id}`, body: ELEMENT },
      { path: `/elements/${other.id}`, body: other },
    ])
    const detail = useElementDetail()
    await detail.open(ELEMENT.id)

    const pending = detail.open(other.id)
    expect(detail.element.value).toBeNull()
    await pending

    expect(detail.element.value?.name).toBe('Grand livre')
  })

  it('forgets the element once closed', async () => {
    stubApi([{ path: `/elements/${ELEMENT.id}`, body: ELEMENT }])
    const detail = useElementDetail()
    await detail.open(ELEMENT.id)

    detail.close()

    expect(detail.element.value).toBeNull()
    expect(detail.status.value).toBe('idle')
    expect(detail.error.value).toBe('')
  })
})
