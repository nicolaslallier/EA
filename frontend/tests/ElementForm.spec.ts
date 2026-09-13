import { fireEvent, render, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import ElementForm from '../src/features/elements/ElementForm.vue'
import { anElement } from './support/api'

const LAYER_GROUPS = [
  {
    layer: 'application' as const,
    types: [
      {
        value: 'application_component' as const,
        label: 'Application Component',
        layer: 'application' as const,
        aspect: 'active_structure' as const,
      },
      {
        value: 'application_service' as const,
        label: 'Application Service',
        layer: 'application' as const,
        aspect: 'behavior' as const,
      },
    ],
  },
]

function renderForm(props: Record<string, unknown> = {}) {
  return render(ElementForm, { props: { layerGroups: LAYER_GROUPS, ...props } })
}

async function fill(label: RegExp, value: string) {
  await fireEvent.update(screen.getByLabelText(label), value)
}

describe('ElementForm', () => {
  it('emits the element to create, type included', async () => {
    const rendered = renderForm()

    await fill(/nom/i, 'Facturation')
    await fill(/^type/i, 'application_service')
    await fill(/description/i, 'Émet les factures')
    await fireEvent.submit(screen.getByRole('form'))

    expect(rendered.emitted().submit[0]).toEqual([
      {
        element_type: 'application_service',
        name: 'Facturation',
        description: 'Émet les factures',
        documentation: '',
        properties: {},
      },
    ])
  })

  it('refuses a blank name without calling the backend', async () => {
    const rendered = renderForm()

    await fireEvent.submit(screen.getByRole('form'))

    expect(rendered.emitted().submit).toBeUndefined()
    expect(screen.getByText(/nom est obligatoire/i)).toBeInTheDocument()
  })

  it('prefills every field when editing, and locks the type', () => {
    renderForm({
      element: anElement({ name: 'Facturation', description: 'Émet les factures' }),
    })

    expect(screen.getByLabelText(/nom/i)).toHaveValue('Facturation')
    expect(screen.getByLabelText(/description/i)).toHaveValue('Émet les factures')
    expect(screen.getByLabelText(/^type/i)).toBeDisabled()
    expect(screen.getByText(/type d'un élément ne se change pas/i)).toBeInTheDocument()
  })

  it('emits only the fields an update may carry, without the type', async () => {
    const rendered = renderForm({ element: anElement({ name: 'Facturation' }) })

    await fill(/nom/i, 'Grand livre')
    await fireEvent.submit(screen.getByRole('form'))

    expect(rendered.emitted().submit[0]).toEqual([
      { name: 'Grand livre', description: '', documentation: '', properties: {} },
    ])
  })

  it('carries the user-defined attributes that were added', async () => {
    const rendered = renderForm()

    await fill(/nom/i, 'Facturation')
    await fireEvent.click(screen.getByRole('button', { name: /ajouter un attribut/i }))
    await fireEvent.update(screen.getByLabelText(/clé de l'attribut 1/i), 'owner')
    await fireEvent.update(screen.getByLabelText(/valeur de l'attribut 1/i), 'finance')
    await fireEvent.submit(screen.getByRole('form'))

    expect(rendered.emitted().submit[0] as unknown[]).toMatchObject([
      { properties: { owner: 'finance' } },
    ])
  })

  it('shows the attributes an element already has, and can drop one', async () => {
    const rendered = renderForm({
      element: anElement({ properties: { owner: 'finance' } }),
    })

    expect(screen.getByLabelText(/clé de l'attribut 1/i)).toHaveValue('owner')

    await fireEvent.click(screen.getByRole('button', { name: /retirer l'attribut 1/i }))
    await fireEvent.submit(screen.getByRole('form'))

    expect(rendered.emitted().submit[0] as unknown[]).toMatchObject([{ properties: {} }])
  })

  it('rejects an attribute name the graph cannot store as a property', async () => {
    const rendered = renderForm()

    await fill(/nom/i, 'Facturation')
    await fireEvent.click(screen.getByRole('button', { name: /ajouter un attribut/i }))
    await fireEvent.update(screen.getByLabelText(/clé de l'attribut 1/i), 'coût €')
    await fireEvent.submit(screen.getByRole('form'))

    expect(rendered.emitted().submit).toBeUndefined()
    expect(screen.getByText(/identifiant simple/i)).toBeInTheDocument()
  })

  it('shows the failure the backend reported instead of losing what was typed', () => {
    renderForm({ failure: 'Facturation existe déjà' })

    expect(screen.getByRole('alert')).toHaveTextContent('Facturation existe déjà')
  })

  it('can be cancelled', async () => {
    const rendered = renderForm()

    await fireEvent.click(screen.getByRole('button', { name: /annuler/i }))

    expect(rendered.emitted().cancel).toHaveLength(1)
  })
})
