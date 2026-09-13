import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import ElementCatalogue from '../src/features/elements/ElementCatalogue.vue'
import { createAppRouter } from '../src/router'
import { METAMODEL, aGraph, aPage, anElement, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const PALETTE: Route = { path: '/metamodel', body: METAMODEL }

async function renderCatalogue(routes: Route[], query = '') {
  const calls = stubApi([PALETTE, ...routes])
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/elements${query}`)
  await router.isReady()
  return { ...render(ElementCatalogue, { global: { plugins: [router] } }), calls, router }
}

async function rowFor(name: RegExp) {
  return within(await screen.findByRole('row', { name }))
}

describe('ElementCatalogue', () => {
  it('lists the catalogue with the human label of each type', async () => {
    await renderCatalogue([
      {
        path: '/elements',
        body: aPage([anElement({ name: 'Facturation', element_type: 'application_component' })]),
      },
    ])

    // Queried inside the row: the filter select also lists every type label.
    const row = await rowFor(/Facturation/)
    expect(row.getByText('Application Component')).toBeInTheDocument()
    expect(row.getByText('Application')).toBeInTheDocument()
  })

  it('says the catalogue is empty rather than showing a bare table', async () => {
    await renderCatalogue([{ path: '/elements', body: aPage([]) }])

    expect(await screen.findByText(/aucun élément/i)).toBeInTheDocument()
  })

  it('reports an unreachable backend', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )
    const router = createAppRouter(createMemoryHistory())
    await router.push('/elements')
    await router.isReady()
    render(ElementCatalogue, { global: { plugins: [router] } })

    expect(await screen.findByRole('alert')).toHaveTextContent(/injoignable/i)
  })

  it('asks the backend again when a filter is applied', async () => {
    const { calls } = await renderCatalogue([{ path: '/elements', body: aPage([anElement()]) }])
    await screen.findByText('Facturation')

    await fireEvent.update(screen.getByLabelText(/rechercher/i), 'factu')
    await fireEvent.submit(screen.getByRole('search'))

    await waitFor(() =>
      expect(calls.at(-1)?.url.searchParams.get('search')).toBe('factu'),
    )
  })

  it('creates an element through the form and shows the refreshed catalogue', async () => {
    const { calls } = await renderCatalogue([
      { path: '/elements', body: aPage([anElement({ name: 'Facturation' })]) },
      { method: 'POST', path: '/elements', status: 201, body: anElement() },
    ])
    await screen.findByText('Facturation')

    await fireEvent.click(screen.getByRole('button', { name: /nouvel élément/i }))
    await fireEvent.update(screen.getByLabelText(/nom/i), 'Grand livre')
    await fireEvent.submit(screen.getByRole('form'))

    await waitFor(() => expect(calls.some((call) => call.method === 'POST')).toBe(true))
    // The form closes only once the write succeeded.
    await waitFor(() => expect(screen.queryByRole('form')).not.toBeInTheDocument())
  })

  it('keeps the form open and shows why when the API refuses the element', async () => {
    await renderCatalogue([
      { path: '/elements', body: aPage([]) },
      {
        method: 'POST',
        path: '/elements',
        status: 409,
        body: { error: 'duplicate', detail: 'Facturation existe déjà' },
      },
    ])
    await screen.findByText(/aucun élément/i)

    await fireEvent.click(screen.getByRole('button', { name: /nouvel élément/i }))
    await fireEvent.update(screen.getByLabelText(/nom/i), 'Facturation')
    await fireEvent.submit(screen.getByRole('form'))

    expect(await screen.findByText(/existe déjà/i)).toBeInTheDocument()
    expect(screen.getByRole('form')).toBeInTheDocument()
  })

  it('edits a row through a form prefilled with that element', async () => {
    const element = anElement({ name: 'Facturation' })
    const { calls } = await renderCatalogue([
      { path: '/elements', body: aPage([element]) },
      { method: 'PATCH', path: `/elements/${element.id}`, body: element },
    ])

    const row = await rowFor(/Facturation/)
    await fireEvent.click(row.getByRole('button', { name: /modifier/i }))

    expect(screen.getByLabelText(/nom/i)).toHaveValue('Facturation')

    await fireEvent.update(screen.getByLabelText(/nom/i), 'Grand livre')
    await fireEvent.submit(screen.getByRole('form'))

    await waitFor(() => expect(calls.some((call) => call.method === 'PATCH')).toBe(true))
  })

  it('asks for a confirmation before deleting, and deletes nothing until then', async () => {
    const element = anElement({ name: 'Facturation' })
    const { calls } = await renderCatalogue([
      { path: '/elements', body: aPage([element]) },
      { method: 'DELETE', path: `/elements/${element.id}`, status: 204 },
    ])

    const row = await rowFor(/Facturation/)
    await fireEvent.click(row.getByRole('button', { name: /^supprimer/i }))

    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    expect(screen.getByText(/supprimer « Facturation »/i)).toBeInTheDocument()

    await fireEvent.click(screen.getByRole('button', { name: /confirmer/i }))

    await waitFor(() => expect(calls.some((call) => call.method === 'DELETE')).toBe(true))
  })

  it('a cancelled confirmation deletes nothing', async () => {
    const element = anElement({ name: 'Facturation' })
    const { calls } = await renderCatalogue([
      { path: '/elements', body: aPage([element]) },
      { method: 'DELETE', path: `/elements/${element.id}`, status: 204 },
    ])

    const row = await rowFor(/Facturation/)
    await fireEvent.click(row.getByRole('button', { name: /^supprimer/i }))
    await fireEvent.click(screen.getByRole('button', { name: /renoncer/i }))

    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
  })

  it('pages through a catalogue larger than one page', async () => {
    const { calls } = await renderCatalogue([
      { path: '/elements', body: aPage([anElement()], 60) },
    ])
    await screen.findByText('Facturation')

    expect(screen.getByText(/page 1 sur 3/i)).toBeInTheDocument()
    await fireEvent.click(screen.getByRole('button', { name: /suivante/i }))

    await waitFor(() => expect(calls.at(-1)?.url.searchParams.get('offset')).toBe('25'))
  })

  it('does not offer a previous page on the first one', async () => {
    await renderCatalogue([{ path: '/elements', body: aPage([anElement()], 60) }])
    await screen.findByText('Facturation')

    expect(screen.getByRole('button', { name: /précédente/i })).toBeDisabled()
  })

  it('opens the documents attached to a row, and only one panel at a time', async () => {
    const element = anElement({ name: 'Facturation' })
    await renderCatalogue([
      { path: '/elements', body: aPage([element]) },
      { path: `/elements/${element.id}/documents`, body: [] },
    ])

    const row = await rowFor(/Facturation/)
    await fireEvent.click(row.getByRole('button', { name: /documents de Facturation/i }))

    expect(
      await screen.findByRole('region', { name: /documents de l'élément/i }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: /relations de l'élément/i })).toBeNull()
  })

  it('opens the detail of an element when its name is clicked', async () => {
    const element = anElement({ name: 'Facturation', description: 'Émet les factures' })
    const { calls } = await renderCatalogue([
      { path: '/elements', body: aPage([element]) },
      { path: `/elements/${element.id}`, body: element },
    ])

    const row = await rowFor(/Facturation/)
    await fireEvent.click(row.getByRole('button', { name: 'Facturation' }))

    const panel = await screen.findByRole('region', { name: /détail de l'élément/i })
    expect(within(panel).getByText('Émet les factures')).toBeInTheDocument()
    // Re-read rather than reuse the row: the detail is the fresh element, and
    // a shared link names an element the current page may not even hold.
    expect(calls.some((call) => call.url.pathname === `/elements/${element.id}`)).toBe(true)
  })

  it('names the detailed element in the URL, so the view can be shared', async () => {
    const element = anElement({ name: 'Facturation' })
    const { router } = await renderCatalogue([
      { path: '/elements', body: aPage([element]) },
      { path: `/elements/${element.id}`, body: element },
    ])

    const row = await rowFor(/Facturation/)
    await fireEvent.click(row.getByRole('button', { name: 'Facturation' }))

    await waitFor(() => expect(router.currentRoute.value.query.element).toBe(element.id))
  })

  it('opens the detail straight away when the URL names an element', async () => {
    const element = anElement({ name: 'Facturation' })
    await renderCatalogue(
      [
        { path: '/elements', body: aPage([element]) },
        { path: `/elements/${element.id}`, body: element },
      ],
      `?element=${element.id}`,
    )

    expect(
      await screen.findByRole('region', { name: /détail de l'élément/i }),
    ).toBeInTheDocument()
  })

  it('drops the element from the URL when the detail is closed', async () => {
    const element = anElement({ name: 'Facturation' })
    const { router } = await renderCatalogue(
      [
        { path: '/elements', body: aPage([element]) },
        { path: `/elements/${element.id}`, body: element },
      ],
      `?element=${element.id}`,
    )
    await screen.findByRole('region', { name: /détail de l'élément/i })

    await fireEvent.click(screen.getByRole('button', { name: /fermer/i }))

    await waitFor(() => expect(router.currentRoute.value.query.element).toBeUndefined())
    expect(screen.queryByRole('region', { name: /détail de l'élément/i })).toBeNull()
  })

  it('says why when the detailed element cannot be read', async () => {
    const element = anElement({ name: 'Facturation' })
    await renderCatalogue(
      [
        { path: '/elements', body: aPage([element]) },
        {
          path: `/elements/${element.id}`,
          status: 404,
          body: { error: 'not_found', detail: "Cet élément n'existe pas" },
        },
      ],
      `?element=${element.id}`,
    )

    expect(await screen.findByText(/n'existe pas/i)).toBeInTheDocument()
  })

  it('replaces the detail with the form when the same element is edited', async () => {
    const element = anElement({ name: 'Facturation' })
    await renderCatalogue(
      [
        { path: '/elements', body: aPage([element]) },
        { path: `/elements/${element.id}`, body: element },
      ],
      `?element=${element.id}`,
    )
    await screen.findByRole('region', { name: /détail de l'élément/i })

    const row = await rowFor(/Facturation/)
    await fireEvent.click(row.getByRole('button', { name: /modifier/i }))

    await waitFor(() =>
      expect(screen.queryByRole('region', { name: /détail de l'élément/i })).toBeNull(),
    )
    expect(screen.getByRole('form')).toBeInTheDocument()
  })

  it('opens the relations of a row so an element can be associated to another', async () => {
    const element = anElement({ name: 'Facturation' })
    await renderCatalogue([
      { path: '/elements', body: aPage([element]) },
      { path: `/elements/${element.id}/relationships`, body: aGraph([element], []) },
    ])

    const row = await rowFor(/Facturation/)
    await fireEvent.click(row.getByRole('button', { name: /^relations/i }))

    expect(
      await screen.findByRole('region', { name: /relations de l'élément/i }),
    ).toBeInTheDocument()
    expect(await screen.findByRole('form', { name: /associer un élément/i })).toBeInTheDocument()
  })
})
