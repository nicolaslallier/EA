import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'

import StereotypePanel from '../src/features/elements/StereotypePanel.vue'
import { useMe } from '../src/lib/me'
import { anElement, stubApi, type Route } from './support/api'

afterEach(() => vi.unstubAllGlobals())

const DB = {
  id: 'ssssssss-0000-4000-8000-000000000000',
  profile_id: 'pppppppp-0000-4000-8000-000000000000',
  name: 'database',
  base_type: 'application_component',
  colour: '#ffe0e0',
  icon_key: 'database',
  properties: [
    { key: 'tier', label: 'Niveau', required: true, kind: 'enum', allowed_values: ['1', '2'], template: null, help: '' },
    { key: 'code', label: 'Code', required: false, kind: 'template', allowed_values: [], template: '???', help: 'Trois lettres' },
  ],
}
const OTHER = { ...DB, id: 'oooooooo-0000-4000-8000-000000000000', name: 'router', base_type: 'node', properties: [] }
const ALL: Route = { path: '/stereotypes', body: [DB, OTHER] }

const element = anElement({
  id: 'aaaaaaaa-1111-4111-8111-111111111111',
  properties: { tier: '1' },
  stereotypes: [{ id: DB.id, name: 'database', colour: '#ffe0e0', icon_key: 'database' }],
})

function asAReader(): void {
  vi.mocked(useMe).mockReturnValueOnce({
    me: ref({ username: 'reader', can_write: false, can_approve: false }),
    canWrite: computed(() => false),
    error: ref(null),
    load: vi.fn(() => Promise.resolve()),
  })
}

describe('StereotypePanel', () => {
  it('offers only the stereotypes of the element type that it does not carry yet', async () => {
    stubApi([ALL])
    render(StereotypePanel, { props: { element: anElement({ id: 'x', stereotypes: [] }) } })

    const select = await screen.findByLabelText('Ajouter un stéréotype')
    const options = [...select.querySelectorAll('option')].map((o) => o.textContent?.trim())
    expect(options).toContain('database')
    expect(options).not.toContain('router')
  })

  it('applies a stereotype by replacing the whole set', async () => {
    const calls = stubApi([ALL, { method: 'PUT', path: `/elements/${element.id}/stereotypes`, body: element }])
    const rendered = render(StereotypePanel, { props: { element: { ...element, stereotypes: [] } } })
    await fireEvent.update(await screen.findByLabelText('Ajouter un stéréotype'), DB.id)

    await fireEvent.click(screen.getByRole('button', { name: 'Appliquer' }))

    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ stereotype_ids: [DB.id] }))
    expect(rendered.emitted().changed).toHaveLength(1)
  })

  it('shows the declared properties: a list for an enumeration, the hint of a template, a star for a required one', async () => {
    stubApi([ALL])
    render(StereotypePanel, { props: { element } })

    const tier = await screen.findByLabelText(/Niveau/)
    expect(tier.tagName).toBe('SELECT')
    expect(screen.getByText('*', { selector: '.required' })).toBeInTheDocument()
    expect(screen.getByLabelText(/Code/)).toHaveAttribute('placeholder', expect.stringContaining('???'))
    expect(screen.getByText('Trois lettres')).toBeInTheDocument()
  })

  it('saves a value with the whole property map, since the PATCH replaces it', async () => {
    const calls = stubApi([ALL, { method: 'PATCH', path: `/elements/${element.id}`, body: element }])
    const rendered = render(StereotypePanel, { props: { element } })
    await fireEvent.update(await screen.findByLabelText(/Code/), 'ABC')

    await fireEvent.click(screen.getByRole('button', { name: 'Enregistrer les propriétés' }))

    await waitFor(() =>
      expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ properties: { tier: '1', code: 'ABC' } }),
    )
    expect(rendered.emitted().changed).toHaveLength(1)
  })

  it("shows the API's refusal of a value in the panel", async () => {
    stubApi([
      ALL,
      { method: 'PATCH', path: `/elements/${element.id}`, status: 422,
        body: { error: 'invalid_input', detail: "tier: '9' is not one of 1, 2" } },
    ])
    render(StereotypePanel, { props: { element } })
    await fireEvent.update(await screen.findByLabelText(/Code/), 'ABC')

    await fireEvent.click(screen.getByRole('button', { name: 'Enregistrer les propriétés' }))

    expect(await screen.findByRole('alert')).toHaveTextContent("tier: '9' is not one of 1, 2")
  })

  it('lets a reader see the stereotypes and the values, and change nothing', async () => {
    stubApi([ALL])
    asAReader()
    render(StereotypePanel, { props: { element } })

    expect(await screen.findByText('«database»')).toBeInTheDocument()
    expect(screen.queryByLabelText('Ajouter un stéréotype')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Enregistrer les propriétés' })).toBeNull()
    expect(await screen.findByLabelText(/Niveau/)).toBeDisabled()
  })
})
