import { describe, expect, it } from 'vitest'

import {
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
