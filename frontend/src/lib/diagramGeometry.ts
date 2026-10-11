// Where things are on a diagram someone draws by hand.
//
// The rings of `graphLayout.ts` place every box themselves; here the user does,
// so the geometry left is the small arithmetic around a pointer: turning a
// click into canvas units, dropping a box centred where it was released,
// sizing a box from the corner being dragged, trimming a link to the borders
// of the boxes it joins, and sizing the canvas to hold whatever was placed. A
// box's stored position is its top-left, the way SVG places a `<rect>`; it
// starts at the size the rings use, so an element looks the same on both
// drawings until someone resizes it.
//
// Pure functions, no DOM: the pointer handling is tested by asserting numbers
// rather than by dispatching events at a mounted canvas.
import { BOX_HEIGHT, BOX_WIDTH } from './graphLayout'

export type Point = { x: number; y: number }
export type Size = { width: number; height: number }
/** A box: its top-left and its size, in canvas units. */
export type Rect = Point & Size

/** The smallest canvas, so an empty diagram still has room to drop onto. */
export const MIN_CANVAS = { width: 800, height: 600 }

/** The size a dropped box starts at. */
export const DEFAULT_BOX: Size = { width: BOX_WIDTH, height: BOX_HEIGHT }

/** The smallest a box can be dragged to, still holding one short line of name. */
export const MIN_BOX: Size = { width: 60, height: 30 }

/**
 * A pointer in client pixels to canvas units; `origin` is the canvas's client
 * top-left and `pan` the canvas point shown at that corner.
 */
export function toCanvasPoint(
  client: Point,
  origin: Point,
  zoom: number,
  pan: Point = { x: 0, y: 0 },
): Point {
  return { x: (client.x - origin.x) / zoom + pan.x, y: (client.y - origin.y) / zoom + pan.y }
}

/** The centre of a box. */
export function centreOf(box: Rect): Point {
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
}

/** The top-left of a default-sized box centred on `point`, never left of or above the canvas. */
export function placeCentredAt(point: Point): Point {
  return {
    x: Math.max(0, point.x - DEFAULT_BOX.width / 2),
    y: Math.max(0, point.y - DEFAULT_BOX.height / 2),
  }
}

/** The size of the box at `topLeft` whose bottom-right corner is dragged to `corner`. */
export function resizedTo(topLeft: Point, corner: Point): Size {
  return {
    width: Math.max(MIN_BOX.width, corner.x - topLeft.x),
    height: Math.max(MIN_BOX.height, corner.y - topLeft.y),
  }
}

/** Where the segment from `from` to the centre of `box` crosses its border. */
export function clipToBox(from: Point, box: Rect): Point {
  const centre = centreOf(box)
  const [dx, dy] = [from.x - centre.x, from.y - centre.y]
  // A zero component never reaches that pair of borders, hence Infinity; both
  // zero means `from` is the centre, and the scale falls back to nothing.
  const scale = Math.min(
    dx === 0 ? Infinity : box.width / 2 / Math.abs(dx),
    dy === 0 ? Infinity : box.height / 2 / Math.abs(dy),
  )
  if (!Number.isFinite(scale)) {
    return centre
  }
  return { x: centre.x + dx * scale, y: centre.y + dy * scale }
}

/**
 * The stroke of a link between two boxes, trimmed to their borders so the
 * arrow head stays visible — or `null` when the boxes overlap, since a line
 * between their borders would then run backwards or not at all.
 */
export function edgeSegment(source: Rect, target: Rect): { from: Point; to: Point } | null {
  const overlap =
    source.x < target.x + target.width &&
    target.x < source.x + source.width &&
    source.y < target.y + target.height &&
    target.y < source.y + source.height
  if (overlap) {
    return null
  }
  return {
    from: clipToBox(centreOf(target), source),
    to: clipToBox(centreOf(source), target),
  }
}

/** The canvas size holding every box plus `margin`, and never less than `MIN_CANVAS`. */
export function canvasExtent(boxes: Rect[], margin: number): Size {
  return {
    width: Math.max(MIN_CANVAS.width, ...boxes.map((box) => box.x + box.width + margin)),
    height: Math.max(MIN_CANVAS.height, ...boxes.map((box) => box.y + box.height + margin)),
  }
}

/** How the canvas is looked at: a scale, and the canvas point at its top-left corner. */
export type View = { zoom: number; pan: Point }

export const ZOOM_RANGE = [0.4, 2] as const

export function clampZoom(zoom: number): number {
  return Math.min(ZOOM_RANGE[1], Math.max(ZOOM_RANGE[0], zoom))
}

/** Scale by `factor` keeping the canvas point under `anchor` (px from the canvas corner) fixed. */
export function zoomAt(view: View, factor: number, anchor: Point): View {
  const zoom = clampZoom(view.zoom * factor)
  const under = { x: anchor.x / view.zoom + view.pan.x, y: anchor.y / view.zoom + view.pan.y }
  return { zoom, pan: { x: under.x - anchor.x / zoom, y: under.y - anchor.y / zoom } }
}

/** A box together with the id it is selected by. */
export type Placed = Rect & { id: string }
/** Where a box ends up after an arrangement. */
export type Moved = Point & { id: string }

/** The smallest rectangle holding every box, or `null` for none. */
export function boundingBox(rects: Rect[]): Rect | null {
  if (rects.length === 0) {
    return null
  }
  const left = Math.min(...rects.map((rect) => rect.x))
  const top = Math.min(...rects.map((rect) => rect.y))
  const right = Math.max(...rects.map((rect) => rect.x + rect.width))
  const bottom = Math.max(...rects.map((rect) => rect.y + rect.height))
  return { x: left, y: top, width: right - left, height: bottom - top }
}

/** The view that shows every box in `viewport`, never past the zoom range. */
export function fitView(boxes: Rect[], viewport: Size, margin = 24): View {
  const bounds = boundingBox(boxes)
  if (!bounds) {
    return { zoom: 1, pan: { x: 0, y: 0 } }
  }
  const zoom = clampZoom(
    Math.min(
      (viewport.width - 2 * margin) / bounds.width,
      (viewport.height - 2 * margin) / bounds.height,
    ),
  )
  return { zoom, pan: { x: bounds.x - margin / zoom, y: bounds.y - margin / zoom } }
}

/** The rectangle between two corners, whichever way it was dragged. */
export function rubberBand(from: Point, to: Point): Rect {
  return {
    x: Math.min(from.x, to.x),
    y: Math.min(from.y, to.y),
    width: Math.abs(to.x - from.x),
    height: Math.abs(to.y - from.y),
  }
}

/** The ids of the boxes a rectangle touches, in the order given. */
export function boxesIn(rect: Rect, boxes: Placed[]): string[] {
  return boxes
    .filter(
      (box) =>
        box.x < rect.x + rect.width &&
        rect.x < box.x + box.width &&
        box.y < rect.y + rect.height &&
        rect.y < box.y + box.height,
    )
    .map((box) => box.id)
}

export type AlignMode = 'left' | 'centre' | 'right' | 'top' | 'middle' | 'bottom'
export type DistributeAxis = 'horizontal' | 'vertical'

/** Line the boxes up on one edge or axis of their common bounds. One box stays put. */
export function alignBoxes(boxes: Placed[], mode: AlignMode): Moved[] {
  const bounds = boundingBox(boxes)
  if (!bounds || boxes.length < 2) {
    return boxes.map(({ id, x, y }) => ({ id, x, y }))
  }
  return boxes.map((box) => {
    switch (mode) {
      case 'left':
        return { id: box.id, x: bounds.x, y: box.y }
      case 'right':
        return { id: box.id, x: bounds.x + bounds.width - box.width, y: box.y }
      case 'centre':
        return { id: box.id, x: bounds.x + (bounds.width - box.width) / 2, y: box.y }
      case 'top':
        return { id: box.id, x: box.x, y: bounds.y }
      case 'bottom':
        return { id: box.id, x: box.x, y: bounds.y + bounds.height - box.height }
      case 'middle':
        return { id: box.id, x: box.x, y: bounds.y + (bounds.height - box.height) / 2 }
    }
  })
}

/**
 * Space three or more boxes so the gaps between them are equal, the two
 * outermost staying where they are. Fewer than three: nothing to distribute.
 */
export function distributeBoxes(boxes: Placed[], axis: DistributeAxis): Moved[] {
  if (boxes.length < 3) {
    return boxes.map(({ id, x, y }) => ({ id, x, y }))
  }
  const horizontal = axis === 'horizontal'
  const start = (box: Placed) => (horizontal ? box.x : box.y)
  const size = (box: Placed) => (horizontal ? box.width : box.height)
  const ordered = [...boxes].sort((a, b) => start(a) - start(b) || a.id.localeCompare(b.id))
  const first = start(ordered[0])
  const end = Math.max(...ordered.map((box) => start(box) + size(box)))
  const gap = (end - first - ordered.reduce((sum, box) => sum + size(box), 0)) / (ordered.length - 1)
  let cursor = first
  return ordered.map((box) => {
    const at = cursor
    cursor += size(box) + gap
    return horizontal ? { id: box.id, x: at, y: box.y } : { id: box.id, x: box.x, y: at }
  })
}

const ARROWS: Record<string, Point> = {
  ArrowLeft: { x: -1, y: 0 },
  ArrowRight: { x: 1, y: 0 },
  ArrowUp: { x: 0, y: -1 },
  ArrowDown: { x: 0, y: 1 },
}

/** The move an arrow key asks for — 1 px, 10 with shift — or `null` for any other key. */
export function nudgeDelta(key: string, coarse: boolean): Point | null {
  const unit = ARROWS[key]
  if (!unit) {
    return null
  }
  const step = coarse ? 10 : 1
  return { x: unit.x * step, y: unit.y * step }
}

/** Roughly how wide one character of a box's name is drawn. */
export const CHARACTER_WIDTH = 7.5

/** As much of `name` as a box `width` wide holds: a wider box shows more of it. */
export function shortenToWidth(name: string, width: number): string {
  const limit = Math.max(4, Math.floor(width / CHARACTER_WIDTH))
  return name.length > limit ? `${name.slice(0, limit - 1)}…` : name
}
