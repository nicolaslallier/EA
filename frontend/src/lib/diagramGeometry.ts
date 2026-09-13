// Where things are on a diagram someone draws by hand.
//
// The rings of `graphLayout.ts` place every box themselves; here the user does,
// so the geometry left is the small arithmetic around a pointer: turning a
// click into canvas units, dropping a box centred where it was released,
// trimming a link to the borders of the boxes it joins, and sizing the canvas
// to hold whatever was placed. A box's stored position is its top-left, the
// way SVG places a `<rect>`, and its size is the one the rings use, so an
// element looks the same on both drawings.
//
// Pure functions, no DOM: the pointer handling is tested by asserting numbers
// rather than by dispatching events at a mounted canvas.
import { BOX_HEIGHT, BOX_WIDTH } from './graphLayout'

export type Point = { x: number; y: number }

/** The smallest canvas, so an empty diagram still has room to drop onto. */
export const MIN_CANVAS = { width: 800, height: 600 }

/** A pointer in client pixels to canvas units; `origin` is the canvas's client top-left. */
export function toCanvasPoint(client: Point, origin: Point, zoom: number): Point {
  return { x: (client.x - origin.x) / zoom, y: (client.y - origin.y) / zoom }
}

/** The centre of the box whose top-left is `position`. */
export function centreOf(position: Point): Point {
  return { x: position.x + BOX_WIDTH / 2, y: position.y + BOX_HEIGHT / 2 }
}

/** The top-left of a box centred on `point`, never left of or above the canvas. */
export function placeCentredAt(point: Point): Point {
  return {
    x: Math.max(0, point.x - BOX_WIDTH / 2),
    y: Math.max(0, point.y - BOX_HEIGHT / 2),
  }
}

/** Where the segment from `from` to the centre of the box at `boxTopLeft` crosses its border. */
export function clipToBox(from: Point, boxTopLeft: Point): Point {
  const centre = centreOf(boxTopLeft)
  const [dx, dy] = [from.x - centre.x, from.y - centre.y]
  // A zero component never reaches that pair of borders, hence Infinity; both
  // zero means `from` is the centre, and the scale falls back to nothing.
  const scale = Math.min(
    dx === 0 ? Infinity : BOX_WIDTH / 2 / Math.abs(dx),
    dy === 0 ? Infinity : BOX_HEIGHT / 2 / Math.abs(dy),
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
export function edgeSegment(source: Point, target: Point): { from: Point; to: Point } | null {
  if (Math.abs(target.x - source.x) < BOX_WIDTH && Math.abs(target.y - source.y) < BOX_HEIGHT) {
    return null
  }
  return {
    from: clipToBox(centreOf(target), source),
    to: clipToBox(centreOf(source), target),
  }
}

/** The canvas size holding every box plus `margin`, and never less than `MIN_CANVAS`. */
export function canvasExtent(positions: Point[], margin: number): { width: number; height: number } {
  return {
    width: Math.max(MIN_CANVAS.width, ...positions.map((p) => p.x + BOX_WIDTH + margin)),
    height: Math.max(MIN_CANVAS.height, ...positions.map((p) => p.y + BOX_HEIGHT + margin)),
  }
}
