// How a stereotype looks on a box: guillemets, an outline, an icon.
//
// Presentation tables only, like `LAYER_LABELS` — not one rule of ArchiMate.
// The icons are a closed set the API validates by name (`IconKey`): a user
// picks one, never uploads one, so no arbitrary SVG ever reaches a page.
import type { components } from '../api/schema'

type StereotypeRef = components['schemas']['StereotypeRef']
type IconKey = components['schemas']['IconKey']

const HEX_COLOUR = /^#[0-9a-fA-F]{6}$/

/** SVG path data on a 16 x 16 grid, drawn with the box's ink. `none` draws nothing. */
export const STEREOTYPE_ICONS: Record<IconKey, string> = {
  none: '',
  database: 'M2 3c0-1.2 2.7-2 6-2s6 .8 6 2v10c0 1.2-2.7 2-6 2s-6-.8-6-2zM2 3c0 1.2 2.7 2 6 2s6-.8 6-2M2 8c0 1.2 2.7 2 6 2s6-.8 6-2',
  server: 'M2 2h12v5H2zM2 9h12v5H2zM4.5 4.5h1M4.5 11.5h1',
  cloud: 'M4.5 13a3 3 0 0 1-.3-6A4 4 0 0 1 12 6.5 3.2 3.2 0 0 1 11.5 13z',
  user: 'M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM2.5 14a5.5 5.5 0 0 1 11 0z',
  lock: 'M3.5 7h9v7h-9zM5.5 7V5a2.5 2.5 0 0 1 5 0v2',
  document: 'M3.5 1.5h6l3 3v10h-9zM9.5 1.5v3h3M5.5 8h5M5.5 11h5',
  gear: 'M8 5.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5zM8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.4 1.4M11.6 11.6 13 13M13 3l-1.4 1.4M4.4 11.6 3 13',
  queue: 'M1.5 4h13v8h-13zM5 4v8M8.5 4v8M12 4v8',
  api: 'M5 4 1.5 8 5 12M11 4l3.5 4L11 12M9.2 3 6.8 13',
}

/** `«a» «b»` for the stereotypes an element carries, or the empty string. */
export function stereotypeLabel(refs: StereotypeRef[]): string {
  return refs.map((ref) => `«${ref.name}»`).join(' ')
}

/** The outline colour: the first stereotype with a valid `#rrggbb`, else nothing. */
export function stereotypeStroke(refs: StereotypeRef[]): string | null {
  return refs.find((ref) => ref.colour !== null && HEX_COLOUR.test(ref.colour))?.colour ?? null
}
