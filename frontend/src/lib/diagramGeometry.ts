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

/** A pointer in client pixels to canvas units; `origin` is the canvas's client top-left. */
export function toCanvasPoint(client: Point, origin: Point, zoom: number): Point {
  return { x: (client.x - origin.x) / zoom, y: (client.y - origin.y) / zoom }
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
