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
  it('greys the boxes whose element is out of period without removing them', () => {
    draw({ inactiveIds: [B.id] })

    const grey = screen.getByRole('button', { name: /Commandes.*hors période/ })
    expect(grey).toHaveClass('canvas__node--inactive')
    expect(screen.getByRole('button', { name: /^Facturation/ })).not.toHaveClass(
      'canvas__node--inactive',
    )
  })

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

describe('DiagramCanvas — a selection of several boxes', () => {
  const both = [A.id, B.id]

  it('toggles a box with shift+click instead of selecting it alone', async () => {
    const rendered = draw()

    await fireEvent.click(screen.getByRole('button', { name: /Facturation/ }), { shiftKey: true })

    expect(rendered.emitted().toggle).toEqual([[A.id]])
    expect(rendered.emitted().select).toBeUndefined()
  })

  it('selects what a dragged band touches', async () => {
    const rendered = draw()

    await fireEvent(svg(), pointer('pointerdown', 250, 90))
    await fireEvent(svg(), pointer('pointermove', 50, -10))
    await fireEvent(svg(), pointer('pointerup', 50, -10))

    expect(rendered.emitted()['select-many']).toEqual([[[A.id]]])
  })

  it('clears the selection when the empty canvas is clicked', async () => {
    const rendered = draw({ selection: both })

    await fireEvent(svg(), pointer('pointerdown', 600, 400))
    await fireEvent(svg(), pointer('pointerup', 600, 400))

    expect(rendered.emitted()['select-many']).toEqual([[[]]])
  })

  it('selects every box on Ctrl+A', async () => {
    const rendered = draw()

    await fireEvent.keyDown(svg(), { key: 'a', ctrlKey: true })

    expect(rendered.emitted()['select-many']).toEqual([[both]])
  })

  it('drags the whole selection when one of its boxes is dragged', async () => {
    const rendered = draw({ selection: both })

    await fireEvent(screen.getByRole('button', { name: /Facturation/ }), pointer('pointerdown', 10, 10))
    await fireEvent(svg(), pointer('pointermove', 110, 60))
    await fireEvent(svg(), pointer('pointerup', 110, 60))

    expect(rendered.emitted().move).toEqual([
      [A.id, { x: 100, y: 50 }],
      [B.id, { x: 400, y: 50 }],
    ])
    expect(rendered.emitted().moved).toHaveLength(1)
  })

  it('drags only the box grabbed when it is not part of the selection', async () => {
    const rendered = draw({ selection: [B.id] })

    await fireEvent(screen.getByRole('button', { name: /Facturation/ }), pointer('pointerdown', 10, 10))
    await fireEvent(svg(), pointer('pointermove', 20, 10))

    expect(rendered.emitted().move).toEqual([[A.id, { x: 10, y: 0 }]])
  })

  it('removes all the selected boxes on Delete', async () => {
    const rendered = draw({ selection: both })

    await fireEvent.keyDown(screen.getByRole('button', { name: /Facturation/ }), { key: 'Delete' })

    expect(rendered.emitted()['remove-many']).toEqual([[both]])
  })

  it('does nothing to a reader: no move, no removal', async () => {
    const rendered = draw({ selection: both, readonly: true })

    await fireEvent(screen.getByRole('button', { name: /Facturation/ }), pointer('pointerdown', 10, 10))
    await fireEvent(svg(), pointer('pointermove', 110, 60))
    await fireEvent.keyDown(screen.getByRole('button', { name: /Facturation/ }), { key: 'Delete' })
    await fireEvent.keyDown(screen.getByRole('button', { name: /Facturation/ }), { key: 'ArrowRight' })

    expect(rendered.emitted().move).toBeUndefined()
    expect(rendered.emitted()['remove-many']).toBeUndefined()
  })
})

describe('DiagramCanvas — the keyboard', () => {
  const key = (name: string, extra: Record<string, unknown> = {}) =>
    fireEvent.keyDown(screen.getByRole('button', { name: /Facturation/ }), { key: name, ...extra })

  it('nudges a box by one pixel, ten with shift', async () => {
    const rendered = draw({ boxes: [{ ...BOXES[0], x: 50, y: 50 }, BOXES[1]] })

    await key('ArrowRight')
    await key('ArrowUp', { shiftKey: true })

    expect(rendered.emitted().move).toEqual([
      [A.id, { x: 51, y: 50 }],
      [A.id, { x: 50, y: 40 }],
    ])
    expect(rendered.emitted().moved).toEqual([['keyboard'], ['keyboard']])
  })

  it('never nudges a box out of the canvas', async () => {
    const rendered = draw()

    await key('ArrowLeft')

    expect(rendered.emitted().move).toEqual([[A.id, { x: 0, y: 0 }]])
  })

  it('resizes with Alt+arrow, never below the minimum box', async () => {
    const rendered = draw()

    await key('ArrowRight', { altKey: true, shiftKey: true })
    await key('ArrowLeft', { altKey: true, shiftKey: true })

    expect(rendered.emitted().resize).toEqual([
      [A.id, { width: 142, height: 46 }],
      [A.id, { width: 122, height: 46 }],
    ])
  })
})

describe('DiagramCanvas — zoom and pan', () => {
  const widthOf = () => Number(svg().getAttribute('width'))
  const originOf = () => svg().getAttribute('viewBox')!.split(' ').map(Number)

  it('zooms with the wheel, around the cursor', async () => {
    draw()

    await fireEvent.wheel(svg(), { deltaY: -100, clientX: 100, clientY: 50 })

    expect(widthOf()).toBeCloseTo(880, 1)
    const [panX, panY] = originOf()
    expect(panX).toBeGreaterThan(0)
    expect(panY).toBeGreaterThan(0)
  })

  it('zooms out with the wheel the other way, never below the floor', async () => {
    draw()

    for (let i = 0; i < 40; i += 1) {
      await fireEvent.wheel(svg(), { deltaY: 100 })
    }

    expect(widthOf()).toBeCloseTo(800 * 0.4, 1)
  })

  it('fits every box in the view, then goes back to 100 %', async () => {
    draw({ boxes: [{ ...BOXES[0], width: 1600 }] })

    await fireEvent.click(screen.getByRole('button', { name: 'Ajuster à la vue' }))
    const fitted = widthOf()
    expect(fitted).toBeLessThan(1600 + 160)

    await fireEvent.click(screen.getByRole('button', { name: '100 %' }))
    expect(widthOf()).toBe(1760)
    expect(originOf().slice(0, 2)).toEqual([0, 0])
  })

  it('pans with the middle button', async () => {
    draw()

    await fireEvent(svg(), new MouseEvent('pointerdown', { clientX: 100, clientY: 100, button: 1, bubbles: true }))
    await fireEvent(svg(), pointer('pointermove', 60, 80))
    await fireEvent(svg(), pointer('pointerup', 60, 80))

    expect(originOf().slice(0, 2)).toEqual([40, 20])
  })

  it('pans with Space held and a drag, and not with a plain drag (that draws a band)', async () => {
    const rendered = draw()

    await fireEvent.keyDown(svg(), { key: ' ' })
    await fireEvent(svg(), pointer('pointerdown', 100, 100))
    await fireEvent(svg(), pointer('pointermove', 90, 100))
    await fireEvent(svg(), pointer('pointerup', 90, 100))
    expect(originOf().slice(0, 2)).toEqual([10, 0])
    expect(rendered.emitted()['select-many']).toBeUndefined()

    await fireEvent.keyUp(svg(), { key: ' ' })
    await fireEvent(svg(), pointer('pointerdown', 100, 100))
    await fireEvent(svg(), pointer('pointermove', 90, 100))
    await fireEvent(svg(), pointer('pointerup', 90, 100))
    expect(rendered.emitted()['select-many']).toHaveLength(1)
  })

  it('maps a drop through the pan and the zoom', async () => {
    const rendered = draw({ boxes: [] })
    await fireEvent.wheel(svg(), { deltaY: -100, clientX: 0, clientY: 0 }) // zoom 1.1 at the corner
    const drop = pointer('drop', 330, 220)
    Object.defineProperty(drop, 'dataTransfer', {
      value: { getData: (type: string) => (type === ELEMENT_DRAG_TYPE ? B.id : '') },
    })

    await fireEvent(screen.getByLabelText(/zone de dessin/i), drop)

    // 330 / 1.1 = 300 and 220 / 1.1 = 200, minus half the default box.
    const [[id, at]] = rendered.emitted().place as [string, { x: number; y: number }][]
    expect(id).toBe(B.id)
    expect(at.x).toBeCloseTo(300 - 66, 6)
    expect(at.y).toBeCloseTo(200 - 23, 6)
  })
})

describe('DiagramCanvas — groups, notes and bends', () => {
  const LINK = 'rrrrrrrr-0000-4000-8000-000000000000'
  const link = aRelationship({ id: LINK, source_id: A.id, target_id: B.id })
  const aGroup = {
    id: 'gggggggg-0000-4000-8000-000000000000',
    kind: 'group' as const,
    x: 0,
    y: 100,
    width: 320,
    height: 200,
    text: 'Domaine Paiement',
    colour: null,
    z: -1,
    parent_id: null,
    anchor_element_id: null,
  }
  const aNote = {
    ...aGroup,
    id: 'nnnnnnnn-0000-4000-8000-000000000000',
    kind: 'note' as const,
    x: 500,
    y: 100,
    width: 180,
    height: 90,
    text: 'À revoir <b>vite</b>',
    z: 1,
    anchor_element_id: B.id,
  }
  const drawn = (props: Record<string, unknown> = {}) =>
    draw({ relationships: [link], objects: [aGroup, aNote], ...props })

  it('draws a group by its title and a note by its text — as text, never as markup', () => {
    drawn()

    expect(screen.getByRole('button', { name: /Groupe « Domaine Paiement »/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Note/ })).toBeInTheDocument()
    // Once in the <title>, once drawn: both are text nodes.
    expect(screen.getAllByText(/À revoir <b>vite<\/b>/).length).toBeGreaterThan(0)
    expect(document.querySelector('b')).toBeNull()
  })

  it('joins a note to the element it is anchored on with a dotted line', () => {
    drawn()

    expect(document.querySelector('.canvas__anchor')).not.toBeNull()
    drawn({ objects: [{ ...aNote, anchor_element_id: null }] })
  })

  it('drags a group and reports where it ends', async () => {
    const rendered = drawn()

    await fireEvent(
      screen.getByRole('button', { name: /Groupe « Domaine Paiement »/ }),
      pointer('pointerdown', 10, 110),
    )
    await fireEvent(svg(), pointer('pointermove', 60, 160))
    await fireEvent(svg(), pointer('pointerup', 60, 160))

    expect(rendered.emitted()['move-object']).toEqual([[aGroup.id, { x: 50, y: 150 }]])
    expect(rendered.emitted().moved).toHaveLength(1)
  })

  it('selects an object on click and removes it on Delete only when selected', async () => {
    const rendered = drawn({ selectedObjectId: aNote.id })
    const note = screen.getByRole('button', { name: /Note/ })

    await fireEvent.click(note)
    await fireEvent.keyDown(note, { key: 'Delete' })

    expect(rendered.emitted()['select-object']).toEqual([[aNote.id]])
    expect(rendered.emitted()['remove-object']).toEqual([[aNote.id]])
  })

  it('nudges a selected note with the arrows', async () => {
    const rendered = drawn({ selectedObjectId: aNote.id })

    await fireEvent.keyDown(screen.getByRole('button', { name: /Note/ }), {
      key: 'ArrowDown',
      shiftKey: true,
    })

    expect(rendered.emitted()['move-object']).toEqual([[aNote.id, { x: 500, y: 110 }]])
    expect(rendered.emitted().moved).toEqual([['keyboard']])
  })

  it('offers a handle in the middle of each segment and starts a bend by dragging it', async () => {
    const rendered = drawn()
    const handles = document.querySelectorAll('.canvas__bend--add')
    expect(handles).toHaveLength(1)

    await fireEvent(handles[0], pointer('pointerdown', 250, 150))
    await fireEvent(svg(), pointer('pointermove', 260, 170))
    await fireEvent(svg(), pointer('pointerup', 260, 170))

    expect(rendered.emitted()['bend-add']).toEqual([[LINK, 0, { x: 250, y: 150 }]])
    expect(rendered.emitted()['bend-move']).toEqual([[LINK, 0, { x: 260, y: 170 }]])
    expect(rendered.emitted().moved).toHaveLength(1)
  })

  it('draws a routed link through its bends, drags a bend and removes it on double click', async () => {
    const rendered = drawn({ routed: [{ relationship_id: LINK, waypoints: [{ x: 250, y: 200 }] }] })
    const line = document.querySelector('.canvas__edge polyline') as SVGPolylineElement
    expect(line.getAttribute('points')!.split(' ')).toHaveLength(3)
    const bend = document.querySelector('.canvas__bend:not(.canvas__bend--add)') as SVGCircleElement

    await fireEvent(bend, pointer('pointerdown', 250, 200))
    await fireEvent(svg(), pointer('pointermove', 255, 205))
    await fireEvent(svg(), pointer('pointerup', 255, 205))
    await fireEvent.dblClick(bend)

    expect(rendered.emitted()['bend-move']).toEqual([[LINK, 0, { x: 255, y: 205 }]])
    expect(rendered.emitted()['bend-remove']).toEqual([[LINK, 0]])
  })

  it('selects a link by clicking its stroke', async () => {
    const rendered = drawn()

    await fireEvent.click(document.querySelector('.canvas__edge polyline')!)

    expect(rendered.emitted()['select-edge']).toEqual([[LINK]])
  })

  it('gives a reader no handle to bend, resize or link', () => {
    drawn({
      readonly: true,
      selectedObjectId: aNote.id,
      routed: [{ relationship_id: LINK, waypoints: [{ x: 250, y: 200 }] }],
    })

    expect(document.querySelector('.canvas__bend')).toBeNull()
    expect(document.querySelector('.canvas__resize')).toBeNull()
  })
})

describe('DiagramCanvas — stereotypes', () => {
  it('writes «name» above the label, outlines the box in the stereotype colour and draws its icon', () => {
    const stereotyped = {
      ...BOXES[0],
      element: {
        ...A,
        stereotypes: [{ id: 's', name: 'database', colour: '#ff0000', icon_key: 'database' }],
      },
    }
    draw({ boxes: [stereotyped, BOXES[1]] })

    expect(screen.getByText('«database»')).toBeInTheDocument()
    const box = screen.getByRole('button', { name: /Facturation/ })
    expect(box.querySelector('rect')?.getAttribute('stroke')).toBe('#ff0000')
    expect(box.querySelector('.canvas__icon')).not.toBeNull()
  })

  it('leaves a box without stereotype exactly as before', () => {
    draw()

    expect(screen.queryByText(/«/)).toBeNull()
    expect(document.querySelector('.canvas__icon')).toBeNull()
  })
})
