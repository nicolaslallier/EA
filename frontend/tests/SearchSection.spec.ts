import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'
import type { Router } from 'vue-router'
import { createMemoryHistory } from 'vue-router'

import { decodeQuery, emptyQuery, type Query } from '../src/features/search/queryState'
import SearchSection from '../src/features/search/SearchSection.vue'
import { useMe } from '../src/lib/me'
import { createAppRouter } from '../src/router'
import {
  METAMODEL,
  aPackage,
  aPage,
  aSavedQuery,
  aSavedQuerySummary,
  anElement,
  deferApi,
  stubApi,
  type Route,
} from './support/api'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const BILLING = anElement({ id: 'e1', name: 'Facturation' })
const SEARCH = (items = [BILLING], total = items.length): Route => ({
  method: 'POST',
  path: '/elements/search',
  body: aPage(items, total),
})
const BASE: Route[] = [
  { path: '/metamodel', body: METAMODEL },
  { path: '/packages', body: [aPackage({ id: 'f', name: 'Finance', path: 'Finance' })] },
  { path: '/queries', body: [aSavedQuerySummary({ id: 'q1', name: 'Les noeuds' })] },
]

async function open(routes: Route[], query = ''): Promise<{ router: Router; calls: ReturnType<typeof stubApi> }> {
  const calls = stubApi([...routes, ...BASE])
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/recherche${query}`)
  await router.isReady()
  render(SearchSection, { global: { plugins: [router] } })
  return { router, calls }
}

function asAReader(): void {
  const reader = {
    me: ref({ username: 'reader', can_write: false, can_approve: false }),
    canWrite: computed(() => false),
    error: ref(null),
    load: vi.fn(() => Promise.resolve()),
  }
  // Only the saved-queries panel asks: the screen leaves writing to it.
  vi.mocked(useMe).mockReturnValueOnce(reader)
}

const q = (changes: Partial<Query>) => encodeURIComponent(JSON.stringify({ ...emptyQuery(), ...changes }))

describe('SearchSection — the address is the question', () => {
  it('asks nothing until there is a question', async () => {
    const { calls } = await open([])

    expect(await screen.findByText(/définis un filtre/i)).toBeInTheDocument()
    expect(calls.some((call) => call.url.pathname === '/elements/search')).toBe(false)
  })

  it('runs the query the address carries and lists what it finds', async () => {
    const { calls } = await open([SEARCH()], `?q=${q({ text: 'fact' })}`)

    const row = await screen.findByRole('row', { name: /Facturation/ })
    expect(within(row).getByRole('link', { name: 'Facturation' }).getAttribute('href')).toContain(
      'element=e1',
    )
    const sent = calls.find((call) => call.url.pathname === '/elements/search')
    expect((sent?.body as Query).text).toBe('fact')
    expect(screen.getByText(/1 résultat/i)).toBeInTheDocument()
  })

  it('says an unreadable address is unreadable and asks nothing', async () => {
    const { calls } = await open([SEARCH()], '?q=pas-du-json')

    expect(await screen.findByRole('alert')).toHaveTextContent(/adresse/i)
    expect(calls.some((call) => call.url.pathname === '/elements/search')).toBe(false)
  })

  it('writes the question into the address when it is submitted, and runs it', async () => {
    const { router, calls } = await open([SEARCH()])
    await screen.findByText(/définis un filtre/i)

    // The palette arrives after the screen: wait for the layer to be offered.
    await fireEvent.click(await screen.findByRole('checkbox', { name: 'Métier' }))
    await fireEvent.submit(screen.getByRole('search', { name: /construire une recherche/i }))

    await waitFor(() => expect(router.currentRoute.value.query.q).toBeDefined())
    const written = decodeQuery(String(router.currentRoute.value.query.q))
    expect(written?.layers).toEqual(['business'])
    await waitFor(() =>
      expect(calls.some((call) => call.url.pathname === '/elements/search')).toBe(true),
    )
  })

  it('does not put a query too long for a link in the address, and proposes to save it', async () => {
    const { router, calls } = await open([SEARCH()])
    await screen.findByText(/définis un filtre/i)

    // Submitting takes the text typed so far, without waiting for the pause.
    await fireEvent.update(screen.getByLabelText(/nom contient/i), 'x'.repeat(2100))
    await fireEvent.submit(screen.getByRole('search', { name: /construire une recherche/i }))

    expect(await screen.findByText(/trop long pour une adresse/i)).toBeInTheDocument()
    expect(router.currentRoute.value.query.q).toBeUndefined()
    await waitFor(() =>
      expect(calls.some((call) => call.url.pathname === '/elements/search')).toBe(true),
    )
  })

  it('pages through the results with the query unchanged', async () => {
    const many = Array.from({ length: 25 }, (_, i) => anElement({ id: `e${i}`, name: `Élément ${i}` }))
    const { calls } = await open([SEARCH(many, 60)], `?q=${q({})}`)
    await screen.findByText('Élément 0')

    await fireEvent.click(screen.getByRole('button', { name: /page suivante/i }))

    await waitFor(() => {
      const sent = calls.filter((call) => call.url.pathname === '/elements/search').at(-1)
      expect((sent?.body as Query).offset).toBe(25)
    })
    expect(screen.getByText(/page 2 sur 3/i)).toBeInTheDocument()
  })

  it('shows the API’s refusal of a filter and leaves the form to fix it', async () => {
    await open(
      [
        {
          method: 'POST',
          path: '/elements/search',
          status: 422,
          body: { error: 'invalid_input', detail: 'the contains test on owner needs a value' },
        },
      ],
      `?q=${q({})}`,
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('needs a value')
    expect(screen.getByRole('search', { name: /construire une recherche/i })).toBeInTheDocument()
  })
})

describe('SearchSection — saved queries', () => {
  const SAVED = aSavedQuery({
    id: 'q1',
    name: 'Les noeuds',
    query: { ...emptyQuery(), text: 'db', element_types: ['node'] },
  })

  it('opens a saved query in the form and runs it by its own endpoint', async () => {
    const { calls } = await open(
      [
        { path: '/queries/q1', body: SAVED },
        { path: '/queries/q1/elements', body: aPage([anElement({ name: 'db-01' })]) },
      ],
      '?saved=q1',
    )

    expect(await screen.findByRole('row', { name: /db-01/ })).toBeInTheDocument()
    expect(screen.getByLabelText(/nom contient/i)).toHaveValue('db')
    expect(calls.some((call) => call.url.pathname === '/queries/q1/elements')).toBe(true)
  })

  it('explains a vanished package and keeps the query editable', async () => {
    await open(
      [
        { path: '/queries/q1', body: SAVED },
        {
          path: '/queries/q1/elements',
          status: 422,
          body: {
            error: 'unknown_package',
            detail: "the query 'Les noeuds' names the package x, which no longer exists",
          },
        },
      ],
      '?saved=q1',
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('no longer exists')
    expect(screen.getByLabelText(/nom contient/i)).toHaveValue('db')
  })

  it('says a stored query that cannot be read cannot be read, without hiding the list', async () => {
    await open(
      [
        {
          path: '/queries/q1',
          status: 422,
          body: { error: 'invalid_query', detail: 'unsupported query version: 2' },
        },
      ],
      '?saved=q1',
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('unsupported query version')
    expect(screen.getByRole('button', { name: 'Les noeuds' })).toBeInTheDocument()
  })

  it('opening one from the list puts it in the address', async () => {
    const { router } = await open([
      { path: '/queries/q1', body: SAVED },
      { path: '/queries/q1/elements', body: aPage([]) },
    ])
    await screen.findByText(/définis un filtre/i)

    await fireEvent.click(await screen.findByRole('button', { name: 'Les noeuds' }))

    await waitFor(() => expect(router.currentRoute.value.query.saved).toBe('q1'))
    expect(router.currentRoute.value.query.q).toBeUndefined()
  })

  it('saves the current filter under a name and opens the new query', async () => {
    const { router, calls } = await open(
      [
        { method: 'POST', path: '/queries', status: 201, body: aSavedQuery({ id: 'new' }) },
        { path: '/queries/new', body: aSavedQuery({ id: 'new' }) },
        { path: '/queries/new/elements', body: aPage([]) },
        SEARCH([]),
      ],
      `?q=${q({ text: 'db' })}`,
    )
    await screen.findByLabelText(/nom de la requête/i)

    await fireEvent.update(screen.getByLabelText(/nom de la requête/i), 'Mes bases')
    await fireEvent.click(screen.getByRole('button', { name: /enregistrer/i }))

    await waitFor(() =>
      expect((calls.find((call) => call.method === 'POST' && call.url.pathname === '/queries')?.body as { query: Query }).query.text).toBe('db'),
    )
    await waitFor(() => expect(router.currentRoute.value.query.saved).toBe('new'))
  })

  it('shows why a name could not be kept', async () => {
    await open(
      [
        {
          method: 'POST',
          path: '/queries',
          status: 409,
          body: { error: 'duplicate', detail: 'a saved query is already named Mes bases' },
        },
        SEARCH([]),
      ],
      `?q=${q({})}`,
    )

    await fireEvent.update(await screen.findByLabelText(/nom de la requête/i), 'Mes bases')
    await fireEvent.click(screen.getByRole('button', { name: /enregistrer/i }))

    expect(await screen.findByText(/already named/)).toBeInTheDocument()
  })

  it('deletes a query after a confirmation', async () => {
    const { calls } = await open([{ method: 'DELETE', path: '/queries/q1', status: 204 }])

    await fireEvent.click(await screen.findByRole('button', { name: /supprimer les noeuds/i }))
    await fireEvent.click(screen.getByRole('button', { name: /confirmer/i }))

    await waitFor(() => expect(calls.some((call) => call.method === 'DELETE')).toBe(true))
  })

  it('gives a reader the shared queries to open, and nothing to write', async () => {
    asAReader()
    await open([{ path: '/queries/q1', body: SAVED }, { path: '/queries/q1/elements', body: aPage([]) }])

    expect(await screen.findByRole('button', { name: 'Les noeuds' })).toBeInTheDocument()
    expect(screen.queryByLabelText(/nom de la requête/i)).toBeNull()
    expect(screen.queryByRole('button', { name: /supprimer/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /renommer/i })).toBeNull()
  })
})

describe('SearchSection — the states of a search, told apart', () => {
  it('says nothing was found, in words, when the answer is an empty page', async () => {
    await open([SEARCH([])], `?q=${q({ text: 'zzz' })}`)

    expect(await screen.findByText(/aucun élément ne correspond/i)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('says it is searching while the answer is on its way, and not that nothing was found', async () => {
    const calls = deferApi()
    const router = createAppRouter(createMemoryHistory())
    await router.push(`/recherche?q=${q({})}`)
    await router.isReady()
    render(SearchSection, { global: { plugins: [router] } })

    await vi.waitFor(() =>
      expect(calls.some((call) => call.url.pathname === '/elements/search')).toBe(true),
    )

    expect(screen.getByText(/recherche…/i)).toBeInTheDocument()
    expect(screen.queryByText(/aucun élément ne correspond/i)).toBeNull()
  })

  it('does not call a refusal "nothing found"', async () => {
    await open(
      [{ method: 'POST', path: '/elements/search', status: 422, body: { error: 'invalid_input', detail: 'nope' } }],
      `?q=${q({})}`,
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('nope')
    expect(screen.queryByText(/aucun élément ne correspond/i)).toBeNull()
    expect(screen.queryByText(/définis un filtre/i)).toBeNull()
  })

  it('says so when the palette could not be loaded, rather than offering an empty form in silence', async () => {
    stubApi([
      { path: '/metamodel', status: 500, body: { error: 'internal_error', detail: 'palette indisponible' } },
      { path: '/packages', body: [] },
      { path: '/queries', body: [] },
    ])
    const router = createAppRouter(createMemoryHistory())
    await router.push('/recherche')
    await router.isReady()
    render(SearchSection, { global: { plugins: [router] } })

    expect(await screen.findByRole('alert')).toHaveTextContent('palette indisponible')
  })
})

describe('SearchSection — the address, walked back and forth', () => {
  it('searching the same filter again runs it again, from the first page', async () => {
    const many = Array.from({ length: 25 }, (_, i) => anElement({ id: `e${i}`, name: `Élément ${i}` }))
    const { calls } = await open([SEARCH(many, 60)], `?q=${q({})}`)
    await screen.findByText('Élément 0')
    await fireEvent.click(screen.getByRole('button', { name: /page suivante/i }))
    const sent = () => calls.filter((call) => call.url.pathname === '/elements/search')
    await waitFor(() => expect(sent()).toHaveLength(2))

    await fireEvent.submit(screen.getByRole('search', { name: /construire une recherche/i }))

    await waitFor(() => expect(sent()).toHaveLength(3))
    expect((sent().at(-1)?.body as Query).offset).toBe(0)
    expect(screen.getByText(/page 1 sur 3/i)).toBeInTheDocument()
  })

  it('goes back to the previous question with the back button', async () => {
    const { router } = await open([SEARCH()], `?q=${q({ text: 'un' })}`)
    await screen.findByRole('row', { name: /Facturation/ })
    await router.push({ query: { q: JSON.stringify({ ...emptyQuery(), text: 'deux' }) } })
    await waitFor(() => expect(screen.getByLabelText(/nom contient/i)).toHaveValue('deux'))

    router.back()

    await waitFor(() => expect(screen.getByLabelText(/nom contient/i)).toHaveValue('un'))
  })

  it('shows the saved query asked last when two addresses follow each other quickly', async () => {
    const calls = deferApi()
    const router = createAppRouter(createMemoryHistory())
    await router.push('/recherche?saved=a')
    await router.isReady()
    render(SearchSection, { global: { plugins: [router] } })
    const call = (path: string) => calls.find((held) => held.url.pathname === path)
    await vi.waitFor(() => expect(call('/queries/a')).toBeDefined())
    await router.push({ query: { saved: 'b' } })
    await vi.waitFor(() => expect(call('/queries/b')).toBeDefined())

    call('/queries/b')?.answer(aSavedQuery({ id: 'b', query: { ...emptyQuery(), text: 'bbb' } }))
    call('/queries/a')?.answer(aSavedQuery({ id: 'a', query: { ...emptyQuery(), text: 'aaa' } }))
    await vi.waitFor(() => expect(call('/queries/b/elements')).toBeDefined())
    call('/queries/b/elements')?.answer(aPage([anElement({ name: 'Résultat de B' })]))

    expect(await screen.findByRole('row', { name: /Résultat de B/ })).toBeInTheDocument()
    expect(screen.getByLabelText(/nom contient/i)).toHaveValue('bbb')
    expect(calls.some((held) => held.url.pathname === '/queries/a/elements')).toBe(false)
  })
})

describe('SearchSection — deleting the query that is open', () => {
  it('leaves the address of a query that no longer exists', async () => {
    const { router } = await open(
      [
        { path: '/queries/q1', body: aSavedQuery({ id: 'q1', name: 'Les noeuds' }) },
        { path: '/queries/q1/elements', body: aPage([]) },
        { method: 'DELETE', path: '/queries/q1', status: 204 },
      ],
      '?saved=q1',
    )
    await fireEvent.click(await screen.findByRole('button', { name: /supprimer les noeuds/i }))

    await fireEvent.click(screen.getByRole('button', { name: /confirmer/i }))

    await waitFor(() => expect(router.currentRoute.value.query.saved).toBeUndefined())
    expect(await screen.findByText(/définis un filtre/i)).toBeInTheDocument()
  })
})

describe('SearchSection — paging follows the filter that was run', () => {
  it('pages the submitted filter, not what has been edited in the form since', async () => {
    const many = Array.from({ length: 25 }, (_, i) => anElement({ id: `e${i}`, name: `Élément ${i}` }))
    const { calls } = await open([SEARCH(many, 60)], `?q=${q({ text: 'fact' })}`)
    await screen.findByText('Élément 0')

    await fireEvent.click(await screen.findByRole('checkbox', { name: 'Métier' }))
    await fireEvent.click(screen.getByRole('button', { name: /page suivante/i }))

    await waitFor(() => {
      const sent = calls.filter((call) => call.url.pathname === '/elements/search').at(-1)
      expect((sent?.body as Query).offset).toBe(25)
    })
    const sent = calls.filter((call) => call.url.pathname === '/elements/search').at(-1)
    expect((sent?.body as Query).layers).toEqual([])
    expect((sent?.body as Query).text).toBe('fact')
  })
})

describe('SearchSection — saving takes what has just been typed', () => {
  it('saves the text typed less than a pause ago', async () => {
    const { calls } = await open(
      [
        { method: 'POST', path: '/queries', status: 201, body: aSavedQuery({ id: 'new' }) },
        { path: '/queries/new', body: aSavedQuery({ id: 'new' }) },
        { path: '/queries/new/elements', body: aPage([]) },
        SEARCH([]),
      ],
      `?q=${q({})}`,
    )
    await fireEvent.update(await screen.findByLabelText(/nom de la requête/i), 'Mes bases')
    vi.useFakeTimers()

    await fireEvent.update(screen.getByLabelText(/nom contient/i), 'db')
    await fireEvent.click(screen.getByRole('button', { name: /enregistrer/i }))

    await vi.waitFor(() => {
      const posted = calls.find((call) => call.method === 'POST' && call.url.pathname === '/queries')
      expect((posted?.body as { query: Query }).query.text).toBe('db')
    })
  })

  it('replaces the filter of a saved query with the text typed less than a pause ago', async () => {
    const { calls } = await open(
      [
        { method: 'PATCH', path: '/queries/q1', body: aSavedQuery({ id: 'q1' }) },
        { path: '/queries/q1', body: aSavedQuery({ id: 'q1' }) },
        { path: '/queries/q1/elements', body: aPage([]) },
        SEARCH([]),
      ],
      `?q=${q({})}`,
    )
    const replace = await screen.findByRole('button', { name: /remplacer le filtre de les noeuds/i })
    vi.useFakeTimers()

    await fireEvent.update(screen.getByLabelText(/nom contient/i), 'db')
    await fireEvent.click(replace)

    await vi.waitFor(() => {
      const patched = calls.find((call) => call.method === 'PATCH')
      expect((patched?.body as { query: Query }).query.text).toBe('db')
    })
  })
})
