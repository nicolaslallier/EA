import { fireEvent, render, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import DiagramCanvas from '../src/features/diagrams/DiagramCanvas.vue'
import { ELEMENT_DRAG_TYPE } from '../src/features/diagrams/useDiagram'
import { MIN_BOX } from '../src/lib/diagramGeometry'
import { anElement, aRelationship } from './support/api'

const A = anElement({ id: 'aaaaaaaa-1111-4111-8111-111111111111', name: 'Facturation' })
const B = anElement({
  id: 'bbbbbbbb-2222-4222-8222-222222222222',
  name: 'Commandes',
  element_type: 'business_process',
  layer: 'business',
})

const BOXES = [
  { element: A, x: 0, y: 0, width: 132, height: 46 },
  { element: B, x: 300, y: 0, width: 132, height: 46 },
]

function draw(props: Record<string, unknown> = {}) {
  return render(DiagramCanvas, {
    props: {
      boxes: BOXES,
      relationships: [aRelationship({ source_id: A.id, target_id: B.id })],
      selectedId: '',
      typeLabel: (value: string) => value,
      ...props,
    },
  })
}

/** jsdom has no PointerEvent nor DragEvent; a MouseEvent of that name carries the coordinates. */
function pointer(type: string, x: number, y: number): MouseEvent {
  return new MouseEvent(type, { clientX: x, clientY: y, button: 0, bubbles: true, cancelable: true })
}

function svg(): SVGSVGElement {
  return document.querySelector('svg') as SVGSVGElement
}

describe('DiagramCanvas', () => {
  it('draws one box per element and each link as its verb', () => {
    draw()

    expect(screen.getByRole('button', { name: /Facturation/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Commandes/ })).toBeInTheDocument()
    expect(screen.getByText('sert')).toBeInTheDocument()
  })

  it('places a dropped element centred where it was released', async () => {
    const rendered = draw({ boxes: [] })
    const drop = pointer('drop', 300, 200)
    Object.defineProperty(drop, 'dataTransfer', {
      value: { getData: (type: string) => (type === ELEMENT_DRAG_TYPE ? B.id : '') },
    })

    await fireEvent(screen.getByLabelText(/zone de dessin/i), drop)

    // The box is 132 x 46: its top-left sits half a box up and left of the pointer.
    expect(rendered.emitted().place).toEqual([[B.id, { x: 234, y: 177 }]])
  })

  it('ignores a drop that carries no element', async () => {
    const rendered = draw({ boxes: [] })
    const drop = pointer('drop', 300, 200)
    Object.defineProperty(drop, 'dataTransfer', { value: { getData: () => '' } })

    await fireEvent(screen.getByLabelText(/zone de dessin/i), drop)

    expect(rendered.emitted().place).toBeUndefined()
  })

  it('selects a box by click and from the keyboard', async () => {
    const rendered = draw()

    await fireEvent.click(screen.getByRole('button', { name: /Facturation/ }))
    await fireEvent.keyDown(screen.getByRole('button', { name: /Commandes/ }), { key: 'Enter' })

    expect(rendered.emitted().select).toEqual([[A.id], [B.id]])
  })

  it('takes the selected box off the diagram with the Delete key', async () => {
    const rendered = draw({ selectedId: A.id })

    await fireEvent.keyDown(screen.getByRole('button', { name: /Commandes/ }), { key: 'Delete' })
    await fireEvent.keyDown(screen.getByRole('button', { name: /Facturation/ }), { key: 'Delete' })

    expect(rendered.emitted().remove).toEqual([[A.id]])
  })

  it('moves a box with the pointer, and says when the move is over', async () => {
    const rendered = draw()

    await fireEvent(screen.getByRole('button', { name: /Facturation/ }), pointer('pointerdown', 20, 10))
    await fireEvent(svg(), pointer('pointermove', 120, 70))
    await fireEvent(svg(), pointer('pointerup', 120, 70))

    expect(rendered.emitted().move).toEqual([[A.id, { x: 100, y: 60 }]])
    expect(rendered.emitted().moved).toHaveLength(1)
  })

  it.each(['pointercancel', 'lostpointercapture'])(
    'still reports a move the system cut short with %s, and follows the pointer no further',
    async (interruption) => {
      const rendered = draw()

      await fireEvent(screen.getByRole('button', { name: /Facturation/ }), pointer('pointerdown', 20, 10))
      await fireEvent(svg(), pointer('pointermove', 120, 70))
      await fireEvent(svg(), pointer(interruption, 120, 70))
      await fireEvent(svg(), pointer('pointermove', 400, 300))

      expect(rendered.emitted().moved).toHaveLength(1)
      expect(rendered.emitted().move).toEqual([[A.id, { x: 100, y: 60 }]])
    },
  )

  it('draws each box at its own size', () => {
    draw({ boxes: [{ element: A, x: 0, y: 0, width: 240, height: 90 }] })

    const rect = document.querySelector('.canvas__node rect') as SVGRectElement
    expect([rect.getAttribute('width'), rect.getAttribute('height')]).toEqual(['240', '90'])
  })

  it('resizes the selected box by dragging its corner, and says when it is over', async () => {
    const rendered = draw({ selectedId: A.id })

    await fireEvent(screen.getByLabelText(/redimensionner « Facturation »/i), pointer('pointerdown', 132, 46))
    await fireEvent(svg(), pointer('pointermove', 232, 106))
    await fireEvent(svg(), pointer('pointerup', 232, 106))

    expect(rendered.emitted().resize).toEqual([[A.id, { width: 232, height: 106 }]])
    expect(rendered.emitted().move).toBeUndefined()
    expect(rendered.emitted().moved).toHaveLength(1)
  })

  it('never shrinks a box below the smallest size', async () => {
    const rendered = draw({ selectedId: A.id })

    await fireEvent(screen.getByLabelText(/redimensionner « Facturation »/i), pointer('pointerdown', 132, 46))
    await fireEvent(svg(), pointer('pointermove', 5, 5))

    expect(rendered.emitted().resize).toEqual([[A.id, MIN_BOX]])
  })

  it('asks for no link when a link gesture is cut short', async () => {
    const rendered = draw({ selectedId: A.id })

    await fireEvent(screen.getByLabelText(/relier « Facturation »/i), pointer('pointerdown', 132, 23))
    await fireEvent(svg(), pointer('pointercancel', 350, 20))

    expect(rendered.emitted().connect).toBeUndefined()
    expect(document.querySelector('.canvas__band')).toBeNull()
  })

  it('read-only, lets a box be selected but never dropped, moved, linked or removed', async () => {
    const rendered = draw({ readonly: true, selectedId: A.id })
    const box = screen.getByRole('button', { name: /Facturation/ })
    const drop = pointer('drop', 300, 200)
    Object.defineProperty(drop, 'dataTransfer', { value: { getData: () => B.id } })

    expect(screen.queryByLabelText(/relier « Facturation »/i)).toBeNull()
    expect(screen.queryByLabelText(/redimensionner « Facturation »/i)).toBeNull()
    await fireEvent(svg(), drop)
    await fireEvent(box, pointer('pointerdown', 20, 10))
    await fireEvent(svg(), pointer('pointermove', 120, 70))
    await fireEvent(svg(), pointer('pointerup', 120, 70))
    await fireEvent.keyDown(box, { key: 'Delete' })
    await fireEvent.click(box)

    const emitted = rendered.emitted()
    expect([emitted.place, emitted.move, emitted.moved, emitted.remove]).toEqual([
      undefined,
      undefined,
      undefined,
      undefined,
    ])
    expect(emitted.select).toEqual([[A.id]])
  })

  it('does not report a move when the box was only clicked', async () => {
    const rendered = draw()

    await fireEvent(screen.getByRole('button', { name: /Facturation/ }), pointer('pointerdown', 20, 10))
    await fireEvent(svg(), pointer('pointerup', 20, 10))

    expect(rendered.emitted().moved).toBeUndefined()
  })

  it('asks to link two boxes when the handle is dragged onto the other one', async () => {
    const rendered = draw({ selectedId: A.id })

    await fireEvent(screen.getByLabelText(/relier « Facturation »/i), pointer('pointerdown', 132, 23))
    await fireEvent(svg(), pointer('pointermove', 250, 23))
    expect(document.querySelector('.canvas__band')).not.toBeNull()
    await fireEvent(svg(), pointer('pointerup', 350, 20))

    expect(rendered.emitted().connect).toEqual([[A.id, B.id]])
    expect(document.querySelector('.canvas__band')).toBeNull()
  })

  it('asks nothing when the handle is released on empty canvas', async () => {
    const rendered = draw({ selectedId: A.id })

    await fireEvent(screen.getByLabelText(/relier « Facturation »/i), pointer('pointerdown', 132, 23))
    await fireEvent(svg(), pointer('pointerup', 700, 500))

    expect(rendered.emitted().connect).toBeUndefined()
  })

  it('zooms the drawing in and out', async () => {
    draw()
    const width = () => Number(svg().getAttribute('width'))
    const before = width()

    await fireEvent.click(screen.getByRole('button', { name: 'Zoomer' }))
    expect(width()).toBeGreaterThan(before)

    await fireEvent.click(screen.getByRole('button', { name: 'Dézoomer' }))
    expect(width()).toBe(before)
  })
})
