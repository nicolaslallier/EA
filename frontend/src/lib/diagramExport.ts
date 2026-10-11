// A diagram as an image.
//
// The SVG is built here as a string rather than copied from the live DOM: the
// screen's SVG leans on CSS variables that invert in the dark theme, and an
// export that changed colour with the viewer's theme would not be a document.
// So the palette is fixed and light, every colour is inline, and every piece
// of text is escaped — names are typed by people, and the file ends up opened
// by other people's tools. No script, no external reference, ever.
// The PNG is that same SVG drawn on a <canvas>; the SVG is the reference.
import { shortenToWidth, type Point, type Size } from './diagramGeometry'

export type ExportBox = {
  id: string
  name: string
  type: string
  fill: string
  x: number
  y: number
  width: number
  height: number
}
export type ExportEdge = { id: string; points: Point[]; label: string }
export type ExportObject = {
  id: string
  text: string
  fill: string
  x: number
  y: number
  width: number
  height: number
}
export type ExportScene = {
  boxes: ExportBox[]
  edges: ExportEdge[]
  groups?: ExportObject[]
  notes?: ExportObject[]
}
export type ExportTheme = { background: string; ink: string; stroke: string; font: string }
export type ExportFormat = 'svg' | 'png' | 'png2'

export const LIGHT_THEME: ExportTheme = {
  background: '#ffffff',
  ink: '#24202b',
  stroke: '#5b5666',
  font: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
}

/** White space around the content, in canvas units. */
export const EXPORT_MARGIN = 24

const HEX_COLOUR = /^#[0-9a-fA-F]{6}$/

export function escapeXml(text: string): string {
  return text
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&apos;')
}

/** A colour that is certainly `#rrggbb`, or `fallback`: a colour is an attribute value. */
export function safeColour(value: string, fallback: string): string {
  return HEX_COLOUR.test(value) ? value : fallback
}

const num = (value: number) => (Number.isFinite(value) ? String(Math.round(value * 100) / 100) : '0')

function bounds(scene: ExportScene): { x: number; y: number; width: number; height: number } {
  const objects = [...(scene.groups ?? []), ...(scene.notes ?? [])]
  const xs = [
    ...scene.boxes.flatMap((box) => [box.x, box.x + box.width]),
    ...objects.flatMap((o) => [o.x, o.x + o.width]),
    ...scene.edges.flatMap((edge) => edge.points.map((point) => point.x)),
  ]
  const ys = [
    ...scene.boxes.flatMap((box) => [box.y, box.y + box.height]),
    ...objects.flatMap((o) => [o.y, o.y + o.height]),
    ...scene.edges.flatMap((edge) => edge.points.map((point) => point.y)),
  ]
  if (xs.length === 0) {
    return { x: 0, y: 0, width: 0, height: 0 }
  }
  const [left, top] = [Math.min(...xs), Math.min(...ys)]
  return { x: left, y: top, width: Math.max(...xs) - left, height: Math.max(...ys) - top }
}

/** The image size: the content plus a margin on every side. */
export function exportSize(scene: ExportScene): Size {
  const content = bounds(scene)
  return { width: content.width + 2 * EXPORT_MARGIN, height: content.height + 2 * EXPORT_MARGIN }
}

export function serializeSvg(scene: ExportScene, theme: ExportTheme = LIGHT_THEME): string {
  const content = bounds(scene)
  const size = exportSize(scene)
  const origin = { x: content.x - EXPORT_MARGIN, y: content.y - EXPORT_MARGIN }
  const ink = safeColour(theme.ink, '#000000')
  const stroke = safeColour(theme.stroke, '#555555')
  const out: string[] = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    `<svg xmlns="http://www.w3.org/2000/svg" width="${num(size.width)}" height="${num(size.height)}" ` +
      `viewBox="${num(origin.x)} ${num(origin.y)} ${num(size.width)} ${num(size.height)}" ` +
      `font-family="${escapeXml(theme.font)}" font-size="12">`,
    `<rect x="${num(origin.x)}" y="${num(origin.y)}" width="${num(size.width)}" height="${num(size.height)}" fill="${safeColour(theme.background, '#ffffff')}"/>`,
    `<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="${stroke}"/></marker></defs>`,
  ]
  for (const group of scene.groups ?? []) {
    out.push(
      `<rect x="${num(group.x)}" y="${num(group.y)}" width="${num(group.width)}" height="${num(group.height)}" rx="8" fill="${safeColour(group.fill, '#efeaf8')}" fill-opacity="0.55" stroke="${stroke}" stroke-dasharray="6 4"/>`,
      `<text x="${num(group.x + 10)}" y="${num(group.y + 18)}" font-weight="600" fill="${ink}">${escapeXml(shortenToWidth(group.text, group.width - 20))}</text>`,
    )
  }
  for (const edge of scene.edges) {
    if (edge.points.length < 2) {
      continue
    }
    const points = edge.points.map((point) => `${num(point.x)},${num(point.y)}`).join(' ')
    const middle = edge.points[Math.floor((edge.points.length - 1) / 2)]
    const next = edge.points[Math.floor((edge.points.length - 1) / 2) + 1]
    out.push(
      `<polyline points="${points}" fill="none" stroke="${stroke}" stroke-width="1.5" marker-end="url(#arrow)"/>`,
      `<text x="${num((middle.x + next.x) / 2)}" y="${num((middle.y + next.y) / 2 - 4)}" text-anchor="middle" fill="${ink}">${escapeXml(edge.label)}</text>`,
    )
  }
  for (const box of scene.boxes) {
    const centre = box.x + box.width / 2
    out.push(
      `<rect x="${num(box.x)}" y="${num(box.y)}" width="${num(box.width)}" height="${num(box.height)}" rx="6" fill="${safeColour(box.fill, '#e8e6ec')}" stroke="${stroke}"/>`,
      `<text x="${num(centre)}" y="${num(box.y + box.height / 2 - 2)}" text-anchor="middle" font-weight="600" fill="${ink}">${escapeXml(shortenToWidth(box.name, box.width))}</text>`,
      `<text x="${num(centre)}" y="${num(box.y + box.height / 2 + 13)}" text-anchor="middle" font-size="10" fill="${ink}">${escapeXml(shortenToWidth(box.type, box.width))}</text>`,
    )
  }
  for (const note of scene.notes ?? []) {
    out.push(
      `<rect x="${num(note.x)}" y="${num(note.y)}" width="${num(note.width)}" height="${num(note.height)}" rx="3" fill="${safeColour(note.fill, '#fff4b0')}" stroke="${stroke}"/>`,
    )
    const rows = Math.max(1, Math.floor((note.height - 12) / 14))
    note.text
      .split('\n')
      .slice(0, rows)
      .forEach((line, index) => {
        out.push(
          `<text x="${num(note.x + 8)}" y="${num(note.y + 18 + index * 14)}" fill="${ink}">${escapeXml(shortenToWidth(line, note.width - 16))}</text>`,
        )
      })
  }
  out.push('</svg>')
  return out.join('\n')
}

// ponytail: the two functions below need a real browser (Image, <canvas>);
// jsdom has neither, so they are exercised by hand and through a mocked module
// in the section's spec. The SVG above is the tested artefact.
/** Draw an SVG string on a canvas `scale` times larger and return it as a PNG. */
export async function rasterize(svg: string, size: Size, scale: 1 | 2): Promise<Blob> {
  const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml;charset=utf-8' }))
  try {
    const image = new Image()
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve()
      image.onerror = () => reject(new Error("L'image n'a pas pu être dessinée."))
      image.src = url
    })
    const canvas = document.createElement('canvas')
    canvas.width = Math.ceil(size.width * scale)
    canvas.height = Math.ceil(size.height * scale)
    const context = canvas.getContext('2d')
    if (!context) {
      throw new Error("Le navigateur ne sait pas dessiner l'image.")
    }
    context.drawImage(image, 0, 0, canvas.width, canvas.height)
    return await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob(
        (blob) => (blob ? resolve(blob) : reject(new Error("L'image n'a pas pu être produite."))),
        'image/png',
      )
    })
  } finally {
    URL.revokeObjectURL(url)
  }
}

/** Hand a blob to the browser as a download. */
export function download(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}
