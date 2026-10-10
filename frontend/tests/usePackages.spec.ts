import { afterEach, describe, expect, it, vi } from 'vitest'

import { usePackages } from '../src/features/packages/usePackages'
import { aPackage, deferApi, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('usePackages', () => {
  it('reads the flat list and builds the tree from it', async () => {
    stubApi([
      {
        path: '/packages',
        body: [aPackage({ id: 'f', name: 'Finance' }), aPackage({ id: 'b', name: 'Budget', parent_id: 'f' })],
      },
    ])

    const store = usePackages()
    await store.load()

    expect(store.packages.value).toHaveLength(2)
    expect(store.tree.value[0].children[0].package.name).toBe('Budget')
    expect(store.byId.value.get('b')?.name).toBe('Budget')
    expect(store.status.value).toBe('ready')
  })

  it('creates a package then reloads, so the list is the stored one', async () => {
    const calls = stubApi([
      { path: '/packages', body: [] },
      { method: 'POST', path: '/packages', status: 201, body: aPackage({ name: 'RH' }) },
    ])

    const created = await usePackages().create({ name: 'RH', parent_id: null })

    expect(created.name).toBe('RH')
    expect(calls.map((call) => call.method)).toEqual(['POST', 'GET'])
    expect(calls[0].body).toEqual({ name: 'RH', parent_id: null })
  })

  it('sends an explicit null to put a package back at the root', async () => {
    const calls = stubApi([
      { path: '/packages', body: [] },
      { method: 'PATCH', path: '/packages/b', body: aPackage({ id: 'b' }) },
    ])

    await usePackages().update('b', { parent_id: null })

    expect(calls[0].body).toEqual({ parent_id: null })
  })

  it('deletes with the target of the contents when there is one, and without otherwise', async () => {
    const calls = stubApi([
      { path: '/packages', body: [] },
      { method: 'DELETE', path: '/packages/b', status: 204 },
    ])
    const store = usePackages()

    await store.remove('b')
    await store.remove('b', 'root')

    const deletes = calls.filter((call) => call.method === 'DELETE')
    expect(deletes[0].url.searchParams.has('move_contents_to')).toBe(false)
    expect(deletes[1].url.searchParams.get('move_contents_to')).toBe('root')
  })

  it('raises the refusal of a non-empty package for the screen to show', async () => {
    stubApi([
      {
        method: 'DELETE',
        path: '/packages/b',
        status: 409,
        body: { error: 'package_not_empty', detail: 'Le paquet contient 2 éléments.' },
      },
    ])

    await expect(usePackages().remove('b')).rejects.toThrow('Le paquet contient 2 éléments.')
  })

  it('files elements and reloads the counts', async () => {
    const calls = stubApi([
      { path: '/packages', body: [] },
      { method: 'POST', path: '/elements/move', status: 204 },
    ])

    await usePackages().moveElements(['e1', 'e2'], null)

    expect(calls[0].body).toEqual({ element_ids: ['e1', 'e2'], package_id: null })
    expect(calls[1].method).toBe('GET')
  })

  it('keeps the answer to the last load, whichever arrives first', async () => {
    const calls = deferApi()
    const store = usePackages()

    const first = store.load()
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    const second = store.load()
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    calls[1].answer([aPackage({ name: 'Récent' })])
    calls[0].answer([aPackage({ name: 'Ancien' })])
    await Promise.all([first, second])

    expect(store.packages.value.map((p) => p.name)).toEqual(['Récent'])
  })
})
