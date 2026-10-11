import { describe, expect, it } from 'vitest'

import {
  type AlignMode,
  type DistributeAxis,
  ZOOM_RANGE,
  alignBoxes,
  boundingBox,
  boxesIn,
  clampZoom,
  distributeBoxes,
  fitView,
  nudgeDelta,
  rubberBand,
  shortenToWidth,
  zoomAt,
  DEFAULT_BOX,
  MIN_BOX,
  MIN_CANVAS,
  canvasExtent,
  centreOf,
  clipToBox,
  edgeSegment,
  placeCentredAt,
  resizedTo,
  toCanvasPoint,
} from '../src/lib/diagramGeometry'
import { BOX_HEIGHT, BOX_WIDTH } from '../src/lib/graphLayout'

/** A box at the default size, the one every drop starts from. */
const at = (x: number, y: number) => ({ x, y, ...DEFAULT_BOX })

describe('toCanvasPoint', () => {
  it('measures from the canvas origin, in canvas units', () => {
    expect(toCanvasPoint({ x: 300, y: 250 }, { x: 100, y: 50 }, 1)).toEqual({ x: 200, y: 200 })
  })

  it('halves the distances at zoom 2', () => {
    expect(toCanvasPoint({ x: 300, y: 250 }, { x: 100, y: 50 }, 2)).toEqual({ x: 100, y: 100 })
  })
})

describe('centreOf and placeCentredAt', () => {
  it('starts from the box the rings draw', () => {
    expect(DEFAULT_BOX).toEqual({ width: BOX_WIDTH, height: BOX_HEIGHT })
  })

  it('reads a stored position as the top-left of the box, at its own size', () => {
    expect(centreOf({ x: 200, y: 100, width: 300, height: 80 })).toEqual({ x: 350, y: 140 })
  })

  it('places a dropped box centred on the pointer', () => {
    const topLeft = placeCentredAt({ x: 400, y: 300 })

    expect(centreOf({ ...topLeft, ...DEFAULT_BOX })).toEqual({ x: 400, y: 300 })
  })

  it('never places a box above or left of the canvas', () => {
    expect(placeCentredAt({ x: 10, y: 10 })).toEqual({ x: 0, y: 0 })
  })
})

describe('clipToBox', () => {
  const box = at(200, 100) // centre (266, 123)

  it('lands on the left border at mid-height for a point straight to the left', () => {
    expect(clipToBox({ x: 0, y: 123 }, box)).toEqual({ x: 200, y: 123 })
  })

  it('lands on the top border for a point straight above', () => {
    expect(clipToBox({ x: 266, y: 0 }, box)).toEqual({ x: 266, y: 100 })
  })

  it('crosses the border the diagonal actually meets', () => {
    // From 132 left and 92 above the centre, the top border (23 away) is met
    // before the left one (66 away): a quarter of the way in.
    expect(clipToBox({ x: 134, y: 31 }, box)).toEqual({ x: 233, y: 100 })
  })

  it('answers the centre when asked from the centre', () => {
    expect(clipToBox({ x: 266, y: 123 }, box)).toEqual({ x: 266, y: 123 })
  })

  it('reaches the border of a resized box, not of the default one', () => {
    expect(clipToBox({ x: 0, y: 150 }, { x: 200, y: 100, width: 400, height: 100 })).toEqual({
      x: 200,
      y: 150,
    })
  })
})

describe('edgeSegment', () => {
  it('runs from border to border between two boxes side by side', () => {
    expect(edgeSegment(at(0, 0), at(300, 0))).toEqual({
      from: { x: BOX_WIDTH, y: BOX_HEIGHT / 2 },
      to: { x: 300, y: BOX_HEIGHT / 2 },
    })
  })

  it('draws nothing when the boxes overlap', () => {
    expect(edgeSegment(at(0, 0), at(100, 20))).toBeNull()
  })

  it('draws nothing when a box grown wide reaches over the other', () => {
    expect(edgeSegment({ x: 0, y: 0, width: 320, height: 46 }, at(300, 0))).toBeNull()
  })
})

describe('resizedTo', () => {
  it('is the size of the box whose bottom-right corner is at the pointer', () => {
    expect(resizedTo({ x: 100, y: 50 }, { x: 400, y: 150 })).toEqual({ width: 300, height: 100 })
  })

  it('never shrinks below the smallest box, even past its top-left', () => {
    expect(resizedTo({ x: 100, y: 50 }, { x: 0, y: 60 })).toEqual(MIN_BOX)
  })
})

describe('canvasExtent', () => {
  it('is the minimum for an empty diagram', () => {
    expect(canvasExtent([], 36)).toEqual(MIN_CANVAS)
  })

  it('stays at the minimum while every box fits inside it', () => {
    expect(canvasExtent([at(100, 100)], 36)).toEqual(MIN_CANVAS)
  })

  it('grows to contain the farthest box plus the margin, at its own size', () => {
    expect(canvasExtent([at(50, 700), { x: 1000, y: 10, width: 500, height: 46 }], 36)).toEqual({
      width: 1000 + 500 + 36,
      height: 700 + BOX_HEIGHT + 36,
    })
  })
})

const placed = (id: string, x: number, y: number, width = 100, height = 50) => ({
  id,
  x,
  y,
  width,
  height,
})
const at2 = (moved: { id: string; x: number; y: number }[], id: string) =>
  moved.find((box) => box.id === id)!

describe('toCanvasPoint with a pan', () => {
  it('adds the pan to the scaled pointer', () => {
    expect(toCanvasPoint({ x: 300, y: 250 }, { x: 100, y: 50 }, 2, { x: 40, y: 10 })).toEqual({
      x: 140,
      y: 110,
    })
  })

  it('keeps the old three-argument behaviour', () => {
    expect(toCanvasPoint({ x: 300, y: 250 }, { x: 100, y: 50 }, 1)).toEqual({ x: 200, y: 200 })
  })
})

describe('zoomAt', () => {
  it('keeps the canvas point under the cursor where it was', () => {
    const view = zoomAt({ zoom: 1, pan: { x: 0, y: 0 } }, 2, { x: 100, y: 50 })

    expect(view.zoom).toBe(2)
    expect(toCanvasPoint({ x: 100, y: 50 }, { x: 0, y: 0 }, view.zoom, view.pan)).toEqual({
      x: 100,
      y: 50,
    })
  })

  it('never leaves the allowed range', () => {
    expect(zoomAt({ zoom: 1.9, pan: { x: 0, y: 0 } }, 10, { x: 0, y: 0 }).zoom).toBe(ZOOM_RANGE[1])
    expect(clampZoom(0.01)).toBe(ZOOM_RANGE[0])
  })
})

describe('fitView', () => {
  it('shows an empty diagram at 100 %', () => {
    expect(fitView([], { width: 800, height: 600 })).toEqual({ zoom: 1, pan: { x: 0, y: 0 } })
  })

  it('shrinks to hold boxes wider than the viewport', () => {
    const view = fitView([placed('a', 0, 0, 1600, 100)], { width: 800, height: 600 }, 0)

    expect(view.zoom).toBe(0.5)
  })
})

describe('selection rectangle', () => {
  it('normalises a band dragged up and to the left', () => {
    expect(rubberBand({ x: 50, y: 40 }, { x: 10, y: 5 })).toEqual({
      x: 10,
      y: 5,
      width: 40,
      height: 35,
    })
  })

  it('selects every box the band touches, and no other', () => {
    const boxes = [placed('a', 0, 0), placed('b', 300, 0), placed('c', 90, 40)]

    expect(boxesIn({ x: 80, y: 30, width: 40, height: 40 }, boxes)).toEqual(['a', 'c'])
  })

  it('boundingBox is null for nothing and the union otherwise', () => {
    expect(boundingBox([])).toBeNull()
    expect(boundingBox([placed('a', 10, 10), placed('b', 200, 100, 50, 50)])).toEqual({
      x: 10,
      y: 10,
      width: 240,
      height: 140,
    })
  })
})

describe('alignBoxes', () => {
  const a = placed('a', 10, 0)
  const b = placed('b', 60, 40, 40, 50)

  it.each<[AlignMode, number, number]>([
    ['left', 10, 10],
    ['right', 10, 70],
    ['centre', 10, 40],
  ])('aligns on the %s edge of the selection', (mode, ax, bx) => {
    const moved = alignBoxes([a, b], mode)

    expect([at2(moved, 'a').x, at2(moved, 'b').x]).toEqual([ax, bx])
  })

  it('aligns tops, middles and bottoms on the vertical axis', () => {
    expect(at2(alignBoxes([a, b], 'top'), 'b').y).toBe(0)
    expect(at2(alignBoxes([a, b], 'bottom'), 'a').y).toBe(40)
    expect(at2(alignBoxes([a, b], 'middle'), 'a').y).toBe(20)
  })

  it('leaves a lone box where it is', () => {
    expect(alignBoxes([a], 'right')).toEqual([{ id: 'a', x: 10, y: 0 }])
  })
})

describe('distributeBoxes', () => {
  it('equalises the gaps between three boxes, keeping the outer two', () => {
    const boxes = [placed('c', 200, 0, 20), placed('a', 0, 0, 20), placed('b', 30, 0, 20)]

    const moved = distributeBoxes(boxes, 'horizontal')

    expect(at2(moved, 'a').x).toBe(0)
    expect(at2(moved, 'b').x).toBe(100)
    expect(at2(moved, 'c').x).toBe(200)
  })

  it.each<DistributeAxis>(['horizontal', 'vertical'])(
    'does nothing with fewer than three boxes (%s)',
    (axis) => {
      const two = [placed('a', 0, 0), placed('b', 500, 500)]

      expect(distributeBoxes(two, axis)).toEqual([
        { id: 'a', x: 0, y: 0 },
        { id: 'b', x: 500, y: 500 },
      ])
    },
  )
})

describe('nudgeDelta', () => {
  it('moves one pixel, ten with shift', () => {
    expect(nudgeDelta('ArrowRight', false)).toEqual({ x: 1, y: 0 })
    expect(nudgeDelta('ArrowUp', true)).toEqual({ x: 0, y: -10 })
  })

  it('ignores every other key', () => {
    expect(nudgeDelta('a', false)).toBeNull()
  })
})

describe('shortenToWidth', () => {
  it('keeps a name that fits and ellipsises one that does not', () => {
    expect(shortenToWidth('Facturation', 200)).toBe('Facturation')
    expect(shortenToWidth('Facturation clients', 60)).toBe('Factura…')
  })
})
