// The diagram as the export sees it: boxes with their fill, links as strokes.
// Kept apart from `lib/diagramExport.ts` because it reads the metamodel's
// colours and the relationship verbs, which `lib/` does not import.
import { edgeSegment } from '../../lib/diagramGeometry'
import type { ExportScene } from '../../lib/diagramExport'
import { LAYER_COLOURS } from '../metamodel/useMetamodel'
import { verbOf } from '../relationships/labels'
import type { Box, RelationshipRead } from './useDiagram'

export function buildExportScene(
  boxes: Box[],
  relationships: RelationshipRead[],
  typeLabel: (value: string) => string,
): ExportScene {
  const byId = new Map(boxes.map((box) => [box.element.id, box]))
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
    })),
    edges: relationships.flatMap((relationship) => {
      const source = byId.get(relationship.source_id)
      const target = byId.get(relationship.target_id)
      const segment = source && target ? edgeSegment(source, target) : null
      return segment
        ? [
            {
              id: relationship.id,
              points: [segment.from, segment.to],
              label: verbOf(relationship.relationship_type, relationship.access_type),
            },
          ]
        : []
    }),
  }
}
