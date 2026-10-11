import { fireEvent, render, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import ObjectInspector from '../src/features/diagrams/ObjectInspector.vue'

const GROUP = {
  id: 'gggggggg-0000-4000-8000-000000000000',
  kind: 'group' as const,
  x: 0,
  y: 0,
  width: 300,
  height: 200,
  text: 'Domaine',
  colour: null,
  z: 0,
  parent_id: null,
  anchor_element_id: null,
}
const NOTE = { ...GROUP, id: 'nnnnnnnn-0000-4000-8000-000000000000', kind: 'note' as const, text: 'À revoir' }
const CHILDREN = [
  { id: 'a', name: 'Facturation' },
  { id: 'b', name: 'Commandes' },
]

function draw(props: Record<string, unknown>) {
  return render(ObjectInspector, {
    props: { anchors: CHILDREN, children: [], canWrite: true, ...props },
  })
}

describe('ObjectInspector', () => {
  it('edits the text of a note as the user types', async () => {
    const rendered = draw({ item: NOTE })

    await fireEvent.update(screen.getByLabelText('Texte de la note'), 'Voir ADR 0031')

    expect(rendered.emitted().update).toEqual([[{ text: 'Voir ADR 0031' }]])
  })

  it('anchors a note on an element, or lets it go', async () => {
    const rendered = draw({ item: NOTE })

    await fireEvent.update(screen.getByLabelText('Ancrer sur un élément'), 'a')
    await fireEvent.update(screen.getByLabelText('Ancrer sur un élément'), '')

    expect(rendered.emitted().update).toEqual([
      [{ anchor_element_id: 'a' }],
      [{ anchor_element_id: null }],
    ])
  })

  it('offers no anchor for a group, and says removing it keeps its content', () => {
    draw({ item: GROUP })

    expect(screen.queryByLabelText('Ancrer sur un élément')).toBeNull()
    expect(screen.getByRole('button', { name: 'Retirer le groupe' })).toBeInTheDocument()
    expect(screen.getByText(/les éléments qu'il contient restent sur le diagramme/i)).toBeInTheDocument()
  })

  it('links two boxes of a group only after an explicit choice of the pair', async () => {
    const rendered = draw({ item: GROUP, children: CHILDREN })

    expect(screen.getByRole('button', { name: 'Relier le contenu…' })).toBeDisabled()
    await fireEvent.update(screen.getByLabelText('Source'), 'a')
    await fireEvent.update(screen.getByLabelText('Cible'), 'b')
    await fireEvent.click(screen.getByRole('button', { name: 'Relier le contenu…' }))

    expect(rendered.emitted().link).toEqual([['a', 'b']])
  })

  it('straightens a routed link', async () => {
    const rendered = draw({ edge: { relationshipId: 'r', label: 'sert', bends: 2 } })

    await fireEvent.click(screen.getByRole('button', { name: 'Redresser le lien' }))

    expect(rendered.emitted().straighten).toHaveLength(1)
  })

  it('is read-only for a reader', () => {
    draw({ item: NOTE, canWrite: false })

    expect(screen.getByLabelText('Texte de la note')).toBeDisabled()
    expect(screen.queryByRole('button', { name: /Supprimer|Retirer/ })).toBeNull()
  })
})
