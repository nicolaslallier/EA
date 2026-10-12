import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

import InstantiateWizard from '../src/features/diagrams/InstantiateWizard.vue'
import { anElement } from './support/api'

const TEMPLATE = {
  id: 't1',
  name: 'Application + hôte',
  description: '',
  created_at: '2026-10-10T12:00:00Z',
  updated_at: '2026-10-10T12:00:00Z',
  body: {
    version: 1,
    slots: [
      { key: 'app', element_type: 'application_component', label: 'Application', x: 0, y: 0, width: 132, height: 46, stereotype: 'database', parent_key: null },
      { key: 'host', element_type: 'node', label: 'Hôte', x: 0, y: 100, width: 132, height: 46, stereotype: null, parent_key: null },
    ],
    groups: [],
    notes: [],
    links: [{ source_key: 'host', target_key: 'app', relationship_type: 'realization' }],
  },
} as never

function draw(extra: Record<string, unknown> = {}) {
  const search = vi.fn((term: string) => Promise.resolve([anElement({ id: 'e1', name: `${term} Facturation` })]))
  const rendered = render(InstantiateWizard, {
    props: { template: TEMPLATE, search, typeLabel: (v: string) => v, failure: '', ...extra },
  })
  return { rendered, search }
}

describe('InstantiateWizard', () => {
  it('searches an element per slot, filtered on the slot type, and shows the stereotype required', async () => {
    const { search } = draw()

    await fireEvent.update(screen.getByLabelText('Application — chercher un élément'), 'Fac')

    await waitFor(() => expect(search).toHaveBeenCalledWith('Fac', 'application_component'))
    expect(screen.getByText(/«database»/)).toBeInTheDocument()
  })

  it('binds an existing element and leaves a slot out, relationships off by default', async () => {
    const { rendered } = draw()
    await fireEvent.update(screen.getByLabelText('Nom de la vue'), 'Facturation')
    await fireEvent.update(screen.getByLabelText('Application — chercher un élément'), 'Fac')
    await fireEvent.click(await screen.findByRole('button', { name: /Facturation/ }))

    await fireEvent.click(screen.getByRole('button', { name: 'Créer la vue' }))

    expect(rendered.emitted().submit).toEqual([
      [
        {
          name: 'Facturation',
          bindings: [{ slot_key: 'app', element_id: 'e1', create: null }],
          create_relationships: false,
        },
      ],
    ])
  })

  it('creates an element for a slot when asked, with the name given', async () => {
    const { rendered } = draw()
    await fireEvent.update(screen.getByLabelText('Nom de la vue'), 'V')
    await fireEvent.click(screen.getByLabelText("Hôte — créer l'élément"))
    await fireEvent.update(screen.getByLabelText('Hôte — nom du nouvel élément'), 'srv-01')

    await fireEvent.click(screen.getByRole('button', { name: 'Créer la vue' }))

    expect((rendered.emitted().submit[0] as [{ bindings: unknown[] }])[0].bindings).toEqual([
      { slot_key: 'host', element_id: null, create: { name: 'srv-01' } },
    ])
  })

  it('cannot create an element for a slot that requires a stereotype', () => {
    draw()

    expect(screen.getByLabelText("Application — créer l'élément")).toBeDisabled()
  })

  it('creates the template relationships only when the box is ticked', async () => {
    const { rendered } = draw()
    const box = screen.getByLabelText('Créer aussi les relations du modèle')
    expect(box).not.toBeChecked()
    await fireEvent.update(screen.getByLabelText('Nom de la vue'), 'V')
    await fireEvent.click(box)

    await fireEvent.click(screen.getByRole('button', { name: 'Créer la vue' }))

    expect(
      (rendered.emitted().submit[0] as [{ create_relationships: boolean }])[0].create_relationships,
    ).toBe(true)
  })

  it('shows the refusal it is given', () => {
    draw({ failure: "slot 'app' wants a application_component, got node" })

    expect(screen.getByRole('alert')).toHaveTextContent('wants a application_component')
  })
})
