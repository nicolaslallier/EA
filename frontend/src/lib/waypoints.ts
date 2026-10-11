// The route of a link: the bends a person put in it.
//
// A link stays the relationship of the graph (docs/adr/0063); only
// its stroke belongs to the view. With no bend it is the straight segment
// trimmed to the two borders, exactly as before; with bends it leaves each box
// towards the nearest bend. Pure functions, nothing is mutated.
import { clipToBox, edgeSegment, type Point, type Rect } from './diagramGeometry'

/** Mirrors the API's bound on bends per link; the API still decides. */
export const MAX_WAYPOINTS = 20

/** The points of the stroke, source border first, or null when there is nothing sensible to draw. */
export function routeOf(source: Rect, target: Rect, waypoints: Point[]): Point[] | null {
  if (waypoints.length === 0) {
    const segment = edgeSegment(source, target)
    return segment ? [segment.from, segment.to] : null
  }
  const first = waypoints[0]
  const last = waypoints[waypoints.length - 1]
  return [clipToBox(first, source), ...waypoints, clipToBox(last, target)]
}

/** The middle of each segment: where a handle to add a bend sits. */
export function midpoints(route: Point[]): Point[] {
  return route.slice(1).map((point, index) => ({
    x: (route[index].x + point.x) / 2,
    y: (route[index].y + point.y) / 2,
  }))
}

/** A bend added on `segment` (0 = the one leaving the source) sits at that index. */
export function insertWaypoint(waypoints: Point[], segment: number, at: Point): Point[] {
  if (waypoints.length >= MAX_WAYPOINTS) {
    return waypoints
  }
  return [...waypoints.slice(0, segment), at, ...waypoints.slice(segment)]
}

export function moveWaypoint(waypoints: Point[], index: number, to: Point): Point[] {
  return waypoints.map((point, position) => (position === index ? to : point))
}

export function removeWaypoint(waypoints: Point[], index: number): Point[] {
  return waypoints.filter((_, position) => position !== index)
}
