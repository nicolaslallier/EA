import { fireEvent, render, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import NewViewForm from '../src/features/diagrams/NewViewForm.vue'

const QUERIES = [{ id: 'q1', name: 'Applications critiques' }]
const PACKAGES = [{ id: 'p1', path: 'SI / Paiement' }]

function draw() {
  return render(NewViewForm, { props: { queries: QUERIES, packages: PACKAGES } })
}

describe('NewViewForm', () => {
  it('creates a live view of a saved query by default', async () => {
    const rendered = draw()
    await fireEvent.update(screen.getByLabelText('Nom de la vue'), 'Périmètre')
    await fireEvent.update(screen.getByLabelText('Requête enregistrée'), 'q1')

    await fireEvent.click(screen.getByRole('button', { name: 'Créer la vue' }))

    expect(rendered.emitted().create).toEqual([
      [{ name: 'Périmètre', source: { query_id: 'q1', package_id: null, recursive: false }, frozen: false }],
    ])
  })

  it('reads a package, with or without its sub-packages, and can be frozen from the start', async () => {
    const rendered = draw()
    await fireEvent.update(screen.getByLabelText('Nom de la vue'), 'Paiement')
    await fireEvent.click(screen.getByLabelText('Un paquet'))
    await fireEvent.update(screen.getByLabelText('Paquet'), 'p1')
    await fireEvent.click(screen.getByLabelText('Avec les sous-paquets'))
    await fireEvent.click(screen.getByLabelText('Figée'))

    await fireEvent.click(screen.getByRole('button', { name: 'Créer la vue' }))

    expect(rendered.emitted().create).toEqual([
      [{ name: 'Paiement', source: { query_id: null, package_id: 'p1', recursive: true }, frozen: true }],
    ])
  })

  it('does not submit without a name and a source', async () => {
    draw()

    expect(screen.getByRole('button', { name: 'Créer la vue' })).toBeDisabled()
    await fireEvent.update(screen.getByLabelText('Nom de la vue'), 'X')
    expect(screen.getByRole('button', { name: 'Créer la vue' })).toBeDisabled()
  })
})
