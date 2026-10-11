import { describe, expect, it } from 'vitest'

import {
  LIGHT_THEME,
  escapeXml,
  exportSize,
  safeColour,
  serializeSvg,
  type ExportScene,
} from '../src/lib/diagramExport'

const BOX = {
  id: 'a',
  name: 'Facturation',
  type: 'Composant applicatif',
  fill: '#b5ffff',
  x: 10,
  y: 20,
  width: 132,
  height: 46,
}

const SCENE: ExportScene = {
  boxes: [BOX, { ...BOX, id: 'b', name: 'Commandes', x: 300 }],
  edges: [{ id: 'r', points: [{ x: 142, y: 43 }, { x: 300, y: 43 }], label: 'sert' }],
}

describe('escapeXml', () => {
  it('escapes the five characters XML cares about', () => {
    expect(escapeXml(`<a href="x">&'</a>`)).toBe('&lt;a href=&quot;x&quot;&gt;&amp;&apos;&lt;/a&gt;')
  })
})

describe('safeColour', () => {
  it('keeps #rrggbb and replaces anything else, injection included', () => {
    expect(safeColour('#AaBb09', '#000000')).toBe('#AaBb09')
    expect(safeColour('red" onload="x', '#000000')).toBe('#000000')
    expect(safeColour('url(javascript:x)', '#000000')).toBe('#000000')
  })
})

describe('serializeSvg', () => {
  it('is a standalone document with the boxes, the link and its verb', () => {
    const svg = serializeSvg(SCENE)

    expect(svg.startsWith('<?xml version="1.0" encoding="UTF-8"?>')).toBe(true)
    expect(svg).toContain('xmlns="http://www.w3.org/2000/svg"')
    expect(svg).toContain('Facturation')
    expect(svg).toContain('Commandes')
    expect(svg).toContain('<polyline')
    expect(svg).toContain('sert')
    expect(svg).toContain('fill="#b5ffff"')
  })

  it('never lets a name become markup', () => {
    const svg = serializeSvg({
      ...SCENE,
      boxes: [{ ...BOX, name: '<script>alert(1)</script>', fill: '"><script>x</script>' }],
    })

    expect(svg).not.toContain('<script')
    expect(svg).toContain('&lt;script&gt;')
    expect(svg).not.toContain('"><script')
  })

  it('embeds no external reference', () => {
    const svg = serializeSvg(SCENE)

    expect(svg).not.toMatch(/href=|xlink|<image|<use|<foreignObject|https?:\/\/(?!www\.w3\.org)/)
  })

  it('is deterministic', () => {
    expect(serializeSvg(SCENE, LIGHT_THEME)).toBe(serializeSvg(SCENE, LIGHT_THEME))
  })

  it('sizes the image to the content plus a margin', () => {
    const size = exportSize(SCENE)

    expect(size.width).toBe(300 + 132 - 10 + 48)
    expect(size.height).toBe(46 + 48)
  })

  it('draws an empty diagram as a small blank page', () => {
    expect(exportSize({ boxes: [], edges: [] })).toEqual({ width: 48, height: 48 })
  })
})
