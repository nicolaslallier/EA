import { describe, expect, it } from 'vitest'

import { buildExportScene } from '../src/features/diagrams/exportScene'
import { LAYER_COLOURS } from '../src/features/metamodel/useMetamodel'
import { anElement, aRelationship } from './support/api'

const A = anElement({ id: 'aaaaaaaa-1111-4111-8111-111111111111', name: 'Facturation' })
const B = anElement({
  id: 'bbbbbbbb-2222-4222-8222-222222222222',
  name: 'Commandes',
  element_type: 'business_process',
  layer: 'business',
})

describe('buildExportScene', () => {
  const boxes = [
    { element: A, x: 0, y: 0, width: 132, height: 46 },
    { element: B, x: 300, y: 0, width: 132, height: 46 },
  ]

  it('paints each box in its layer colour and names its type', () => {
    const scene = buildExportScene(boxes, [], (value) => `type:${value}`)

    expect(scene.boxes[0]).toMatchObject({ id: A.id, fill: LAYER_COLOURS.application })
    expect(scene.boxes[1]).toMatchObject({ fill: LAYER_COLOURS.business, type: 'type:business_process' })
  })

  it('draws a link between boxes as a trimmed segment with its verb', () => {
    const link = aRelationship({ source_id: A.id, target_id: B.id, relationship_type: 'serving' })

    const scene = buildExportScene(boxes, [link], (value) => value)

    expect(scene.edges).toHaveLength(1)
    expect(scene.edges[0].points).toHaveLength(2)
    expect(scene.edges[0].label).toBe('sert')
  })

  it('leaves out a link whose end is not on the diagram', () => {
    const link = aRelationship({ source_id: A.id, target_id: 'cccccccc-3333-4333-8333-333333333333' })

    expect(buildExportScene(boxes, [link], (value) => value).edges).toEqual([])
  })

  it('exports the bends of a routed link, and the groups and notes with their colours', () => {
    const link = aRelationship({ id: 'r', source_id: A.id, target_id: B.id })
    const scene = buildExportScene(
      boxes,
      [link],
      (value) => value,
      [{ relationship_id: 'r', waypoints: [{ x: 250, y: 150 }] }],
      [
        { id: 'g', kind: 'group', x: 0, y: 0, width: 300, height: 200, text: 'Domaine', colour: '#ffe0e0', z: -1, parent_id: null, anchor_element_id: null },
        { id: 'n', kind: 'note', x: 5, y: 5, width: 100, height: 60, text: 'x', colour: null, z: 1, parent_id: null, anchor_element_id: null },
      ],
    )

    expect(scene.edges[0].points).toHaveLength(3)
    expect(scene.groups?.[0]).toMatchObject({ text: 'Domaine', fill: '#ffe0e0' })
    expect(scene.notes?.[0].fill).toBe('#fff4b0')
  })
})
