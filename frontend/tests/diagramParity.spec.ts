// The static drawing (server) and the editor (this SPA) must put the same
// things at the same coordinates: one fixture, read by both sides.
import { describe, expect, it } from 'vitest'

import { BOX_TEXT, LAYER_COLOURS } from '../src/features/metamodel/useMetamodel'
import { CANVAS_MARGIN, canvasExtent, edgeSegment, shortenToWidth } from '../src/lib/diagramGeometry'
import fixture from './fixtures/diagram-parity.json'

const SVG = new DOMParser().parseFromString(fixture.svg, 'image/svg+xml')
const rects = new Map(
  fixture.nodes.map((node) => [
    node.element_id,
    { x: node.x, y: node.y, width: node.width, height: node.height },
  ]),
)

const near = (value: string | null | undefined, expected: number) =>
  expect(Number(value)).toBeCloseTo(expected, 2)

describe('the server drawing and the editor agree', () => {
  it('has a server drawing to compare with', () => {
    expect(SVG.querySelector('parsererror')).toBeNull()
    expect(fixture.svg.length).toBeGreaterThan(100)
  })

  it('draws every box where the editor places it, in the colour the editor paints', () => {
    for (const node of fixture.nodes) {
      const rect = SVG.querySelector(`g[data-element-id="${node.element_id}"] rect`)
      near(rect?.getAttribute('x'), node.x)
      near(rect?.getAttribute('y'), node.y)
      near(rect?.getAttribute('width'), node.width)
      near(rect?.getAttribute('height'), node.height)
      const element = fixture.elements.find((e) => e.id === node.element_id)
      const layer = element?.layer as keyof typeof LAYER_COLOURS
      expect(rect?.getAttribute('fill')).toBe(LAYER_COLOURS[layer])
    }
  })

  it('sizes the canvas as the editor does', () => {
    const extent = canvasExtent([...rects.values()], CANVAS_MARGIN)

    expect(SVG.documentElement.getAttribute('viewBox')).toBe(
      `0 0 ${extent.width} ${extent.height}`,
    )
  })

  it('trims every link as the editor does, and draws none where boxes overlap', () => {
    for (const link of fixture.relationships) {
      const source = rects.get(link.source_id)
      const target = rects.get(link.target_id)
      const segment = source && target ? edgeSegment(source, target) : null
      const line = SVG.querySelector(`g[data-relationship-id="${link.id}"] line`)
      if (segment === null) {
        expect(line).toBeNull()
        continue
      }
      near(line?.getAttribute('x1'), segment.from.x)
      near(line?.getAttribute('y1'), segment.from.y)
      near(line?.getAttribute('x2'), segment.to.x)
      near(line?.getAttribute('y2'), segment.to.y)
    }
  })

  it('shortens names by the editor rule', () => {
    for (const element of fixture.elements) {
      const width = rects.get(element.id)?.width ?? 0
      const text = SVG.querySelector(`g[data-element-id="${element.id}"] text.name`)?.textContent
      expect(text).toBe(shortenToWidth(element.name, width))
    }
  })

  it('uses the editor palette and ink', () => {
    expect(fixture.layer_colours).toEqual(LAYER_COLOURS)
    expect(fixture.box_ink).toBe(BOX_TEXT)
  })
})
