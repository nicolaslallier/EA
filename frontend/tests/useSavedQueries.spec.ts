import { afterEach, describe, expect, it, vi } from 'vitest'

import { emptyQuery } from '../src/features/search/queryState'
import { useSavedQueries } from '../src/features/search/useSavedQueries'
import { aSavedQuery, aSavedQuerySummary, deferApi, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const LIST = { path: '/queries', body: [aSavedQuerySummary()] }

describe('useSavedQueries', () => {
  it('lists the shared queries', async () => {
    stubApi([LIST])
    const store = useSavedQueries()

    await store.load()

    expect(store.queries.value.map((q) => q.name)).toEqual(['Les noeuds'])
  })

  it('saves the current filter under a name then reloads the list', async () => {
    const calls = stubApi([
      LIST,
      { method: 'POST', path: '/queries', status: 201, body: aSavedQuery({ id: 'new' }) },
    ])
    const query = { ...emptyQuery(), text: 'db' }

    const created = await useSavedQueries().create('Les noeuds', 'Tous', query)

    expect(created.id).toBe('new')
    expect(calls.map((c) => c.method)).toEqual(['POST', 'GET'])
    expect(calls[0].body).toEqual({ name: 'Les noeuds', description: 'Tous', query })
  })

  it('raises a refusal — a name already taken — for the form to show', async () => {
    stubApi([
      {
        method: 'POST',
        path: '/queries',
        status: 409,
        body: { error: 'duplicate', detail: 'a saved query is already named Les noeuds' },
      },
    ])

    await expect(useSavedQueries().create('Les noeuds', '', emptyQuery())).rejects.toThrow(
      'already named',
    )
  })

  it('opens one query with its filter', async () => {
    stubApi([{ path: '/queries/q1', body: aSavedQuery({ id: 'q1' }) }])
    const store = useSavedQueries()

    await store.open('q1')

    expect(store.opened.value?.id).toBe('q1')
    expect(store.openStatus.value).toBe('ready')
  })

  it('keeps an unreadable stored query as a message, not a crash', async () => {
    stubApi([
      {
        path: '/queries/q1',
        status: 422,
        body: { error: 'invalid_query', detail: 'unsupported query version: 2' },
      },
    ])
    const store = useSavedQueries()

    await store.open('q1')

    expect(store.openError.value).toBe('unsupported query version: 2')
    expect(store.opened.value).toBeNull()
  })

  it('renames, replaces the filter and deletes, reloading each time', async () => {
    const calls = stubApi([
      LIST,
      { method: 'PATCH', path: '/queries/q1', body: aSavedQuery({ id: 'q1' }) },
      { method: 'DELETE', path: '/queries/q1', status: 204 },
    ])
    const store = useSavedQueries()

    await store.update('q1', { name: 'Autre' })
    await store.update('q1', { query: emptyQuery() })
    await store.remove('q1')

    expect(calls.filter((c) => c.method === 'PATCH').map((c) => Object.keys(c.body as object))).toEqual([
      ['name'],
      ['query'],
    ])
    expect(calls.filter((c) => c.method === 'GET')).toHaveLength(3)
  })

  it('opens the last one asked for, whichever answer arrives first', async () => {
    const calls = deferApi()
    const store = useSavedQueries()

    const first = store.open('a')
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    const second = store.open('b')
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    calls[1].answer(aSavedQuery({ id: 'b' }))
    calls[0].answer(aSavedQuery({ id: 'a' }))
    await Promise.all([first, second])

    expect(store.opened.value?.id).toBe('b')
  })
})
