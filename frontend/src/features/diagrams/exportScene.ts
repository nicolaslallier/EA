// The diagram as the export sees it: boxes with their fill, links as strokes.
// Kept apart from `lib/diagramExport.ts` because it reads the metamodel's
// colours and the relationship verbs, which `lib/` does not import.
import { LAYER_COLOURS } from '../metamodel/useMetamodel'
import { stereotypeLabel, stereotypeStroke } from '../../lib/stereotypeRender'
import { verbOf } from '../relationships/labels'
import { routeOf } from '../../lib/waypoints'
import type { ExportObject, ExportScene } from '../../lib/diagramExport'
import type { Box, DiagramEdge, DiagramObject, RelationshipRead } from './useDiagram'

const FILLS = { group: '#efeaf8', note: '#fff4b0' } as const

const exported = (item: DiagramObject): ExportObject => ({
  id: item.id,
  text: item.text,
  fill: item.colour ?? FILLS[item.kind],
  x: item.x,
  y: item.y,
  width: item.width,
  height: item.height,
})

export function buildExportScene(
  boxes: Box[],
  relationships: RelationshipRead[],
  typeLabel: (value: string) => string,
  routed: DiagramEdge[] = [],
  objects: DiagramObject[] = [],
): ExportScene {
  const byId = new Map(boxes.map((box) => [box.element.id, box]))
  const bends = new Map(routed.map((edge) => [edge.relationship_id, edge.waypoints]))
  const ordered = [...objects].sort((a, b) => a.z - b.z)
  return {
    boxes: boxes.map((box) => ({
      id: box.element.id,
      name: box.element.name,
      type: typeLabel(box.element.element_type),
      fill: LAYER_COLOURS[box.element.layer],
      x: box.x,
      y: box.y,
      width: box.width,
      height: box.height,
      stereotype: stereotypeLabel(box.element.stereotypes) || undefined,
      stroke: stereotypeStroke(box.element.stereotypes) ?? undefined,
    })),
    edges: relationships.flatMap((relationship) => {
      const source = byId.get(relationship.source_id)
      const target = byId.get(relationship.target_id)
      const points =
        source && target ? routeOf(source, target, bends.get(relationship.id) ?? []) : null
      return points
        ? [
            {
              id: relationship.id,
              points,
              label: verbOf(relationship.relationship_type, relationship.access_type),
            },
          ]
        : []
    }),
    groups: ordered.filter((item) => item.kind === 'group').map(exported),
    notes: ordered.filter((item) => item.kind === 'note').map(exported),
  }
}
