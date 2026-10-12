import { fireEvent, render, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import DiagramToolbar from '../src/features/diagrams/DiagramToolbar.vue'

function draw(props: Record<string, unknown> = {}) {
  return render(DiagramToolbar, {
    props: { canWrite: true, canUndo: true, canRedo: false, selectionCount: 3, ...props },
  })
}

describe('DiagramToolbar', () => {
  it('offers undo and redo, enabled by what the history holds', async () => {
    const rendered = draw()

    expect(screen.getByRole('button', { name: 'Annuler' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Rétablir' })).toBeDisabled()
    await fireEvent.click(screen.getByRole('button', { name: 'Annuler' }))

    expect(rendered.emitted().undo).toHaveLength(1)
  })

  it('aligns with two boxes or more, distributes with three or more', () => {
    draw({ selectionCount: 2 })

    expect(screen.getByRole('button', { name: 'Aligner à gauche' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Répartir horizontalement' })).toBeDisabled()
  })

  it('emits the alignment and the export format asked for', async () => {
    const rendered = draw()

    await fireEvent.click(screen.getByRole('button', { name: 'Aligner à droite' }))
    await fireEvent.click(screen.getByRole('button', { name: 'Exporter en SVG' }))
    await fireEvent.click(screen.getByRole('button', { name: 'Exporter en PNG 2x' }))

    expect(rendered.emitted().align).toEqual([['right']])
    expect(rendered.emitted().export).toEqual([['svg'], ['png2']])
  })

  it('shows a reader the export and nothing that changes the diagram', () => {
    draw({ canWrite: false })

    expect(screen.getByRole('button', { name: 'Exporter en SVG' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Annuler' })).toBeNull()
    expect(screen.queryByRole('button', { name: /Aligner/ })).toBeNull()
  })

  it('offers saving as a template to an editor only', async () => {
    const rendered = draw()
    await fireEvent.click(screen.getByRole('button', { name: 'Enregistrer comme modèle' }))
    expect(rendered.emitted()['save-template']).toHaveLength(1)
  })

  it('hides saving as a template from a reader', () => {
    draw({ canWrite: false })
    expect(screen.queryByRole('button', { name: 'Enregistrer comme modèle' })).toBeNull()
  })
})
