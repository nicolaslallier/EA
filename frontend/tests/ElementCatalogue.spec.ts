import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ElementCatalogue from '../src/features/elements/ElementCatalogue.vue'
import { METAMODEL, aPage, anElement, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const PALETTE: Route = { path: '/metamodel', body: METAMODEL }

function renderCatalogue(routes: Route[]) {
  const calls = stubApi([PALETTE, ...routes])
  return { ...render(ElementCatalogue), calls }
}

async function rowFor(name: RegExp) {
  return within(await screen.findByRole('row', { name }))
}

describe('ElementCatalogue', () => {
  it('lists the catalogue with the human label of each type', async () => {
    renderCatalogue([
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
    renderCatalogue([{ path: '/elements', body: aPage([]) }])

    expect(await screen.findByText(/aucun élément/i)).toBeInTheDocument()
  })

  it('reports an unreachable backend', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )
    render(ElementCatalogue)

    expect(await screen.findByRole('alert')).toHaveTextContent(/injoignable/i)
  })

  it('asks the backend again when a filter is applied', async () => {
    const { calls } = renderCatalogue([{ path: '/elements', body: aPage([anElement()]) }])
    await screen.findByText('Facturation')

    await fireEvent.update(screen.getByLabelText(/rechercher/i), 'factu')
    await fireEvent.submit(screen.getByRole('search'))

    await waitFor(() =>
      expect(calls.at(-1)?.url.searchParams.get('search')).toBe('factu'),
    )
  })

  it('creates an element through the form and shows the refreshed catalogue', async () => {
    const { calls } = renderCatalogue([
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
    renderCatalogue([
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
    const { calls } = renderCatalogue([
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
    const { calls } = renderCatalogue([
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
    const { calls } = renderCatalogue([
      { path: '/elements', body: aPage([element]) },
      { method: 'DELETE', path: `/elements/${element.id}`, status: 204 },
    ])

    const row = await rowFor(/Facturation/)
    await fireEvent.click(row.getByRole('button', { name: /^supprimer/i }))
    await fireEvent.click(screen.getByRole('button', { name: /renoncer/i }))

    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
  })

  it('pages through a catalogue larger than one page', async () => {
    const { calls } = renderCatalogue([
      { path: '/elements', body: aPage([anElement()], 60) },
    ])
    await screen.findByText('Facturation')

    expect(screen.getByText(/page 1 sur 3/i)).toBeInTheDocument()
    await fireEvent.click(screen.getByRole('button', { name: /suivante/i }))

    await waitFor(() => expect(calls.at(-1)?.url.searchParams.get('offset')).toBe('25'))
  })

  it('does not offer a previous page on the first one', async () => {
    renderCatalogue([{ path: '/elements', body: aPage([anElement()], 60) }])
    await screen.findByText('Facturation')

    expect(screen.getByRole('button', { name: /précédente/i })).toBeDisabled()
  })
})
