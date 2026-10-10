import { afterEach, describe, expect, it, vi } from 'vitest'

import { emptyQuery } from '../src/features/search/queryState'
import { useElementSearch } from '../src/features/search/useElementSearch'
import { aPage, anElement, deferApi, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useElementSearch', () => {
  it('posts the query as it is and keeps the page and the total', async () => {
    const calls = stubApi([
      { method: 'POST', path: '/elements/search', body: aPage([anElement({ name: 'Billing' })], 42) },
    ])
    const search = useElementSearch()
    const query = { ...emptyQuery(), text: 'bill' }

    await search.run(query)

    expect(calls[0].body).toEqual(query)
    expect(search.items.value.map((e) => e.name)).toEqual(['Billing'])
    expect(search.total.value).toBe(42)
    expect(search.status.value).toBe('ready')
  })

  it('runs a saved query through its own endpoint, paged', async () => {
    const calls = stubApi([{ path: '/queries/q1/elements', body: aPage([anElement()], 3) }])
    const search = useElementSearch()

    await search.runSaved('q1', 25, 50)

    expect(calls[0].url.searchParams.get('limit')).toBe('25')
    expect(calls[0].url.searchParams.get('offset')).toBe('50')
    expect(search.total.value).toBe(3)
  })

  it('keeps a refusal as a message and forgets the previous results', async () => {
    stubApi([{ method: 'POST', path: '/elements/search', body: aPage([anElement()]) }])
    const search = useElementSearch()
    await search.run(emptyQuery())
    stubApi([
      {
        method: 'POST',
        path: '/elements/search',
        status: 422,
        body: { error: 'invalid_input', detail: 'limit is between 1 and 200' },
      },
    ])

    await search.run(emptyQuery())

    expect(search.error.value).toBe('limit is between 1 and 200')
    expect(search.status.value).toBe('error')
    expect(search.items.value).toEqual([])
  })

  it('shows the answer to the last question, whichever arrives first', async () => {
    const calls = deferApi()
    const search = useElementSearch()

    const first = search.run({ ...emptyQuery(), text: 'a' })
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    const second = search.run({ ...emptyQuery(), text: 'ab' })
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    calls[1].answer(aPage([anElement({ name: 'Récent' })]))
    calls[0].answer(aPage([anElement({ name: 'Ancien' })], 9))
    await Promise.all([first, second])

    expect(search.items.value.map((e) => e.name)).toEqual(['Récent'])
    expect(search.total.value).toBe(1)
  })

  it('clears what it shows and drops the request in flight', async () => {
    const calls = deferApi()
    const search = useElementSearch()
    const pending = search.run(emptyQuery())
    await vi.waitFor(() => expect(calls).toHaveLength(1))

    search.clear()
    await pending

    expect(calls[0].aborted()).toBe(true)
    expect(search.status.value).toBe('idle')
  })
})
