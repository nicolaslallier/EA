// How the dashboard reads what the server decided: a `level` becomes a swatch,
// a sentinel key becomes a phrase. Presentation only — no threshold, no rule.
// The ink of each swatch is stated, never `var(--text)`, which inverts in the
// dark theme and would put pale text on a pale fill (as `BOX_TEXT` does for
// the layer colours).
import { LAYER_LABELS, type Layer } from '../metamodel/useMetamodel'

/** Five steps of one hue, light to dark: the level is also printed in the cell. */
export const LEVEL_FILLS = ['#e3eefa', '#bad6f0', '#86b6e0', '#4a86c5', '#1f5496'] as const
export const LEVEL_INKS = ['#24202b', '#24202b', '#24202b', '#ffffff', '#ffffff'] as const
export const NO_LEVEL = { fill: '#f1f0f3', ink: '#24202b' } as const

export function swatchOf(level: number | null): { fill: string; ink: string } {
  if (level === null || level < 0 || level >= LEVEL_FILLS.length) {
    return NO_LEVEL
  }
  return { fill: LEVEL_FILLS[level], ink: LEVEL_INKS[level] }
}

const UNSET = '__unset__'
const INVALID = '__invalid__'

/**
 * A bucket key as a person reads it. Layer keys go through the metamodel's labels, and `names`
 * (package id → path) names the keys of a package dimension.
 */
export function keyLabel(key: string, names?: ReadonlyMap<string, string>): string {
  if (key === UNSET) {
    return 'Non renseigné'
  }
  if (key === INVALID) {
    return 'Valeur illisible'
  }
  return names?.get(key) ?? (LAYER_LABELS as Record<string, string>)[key] ?? key
}

/**
 * The catalogue filter a cell stands for, or null when none applies.
 *
 * The catalogue filters on a layer and on an element type, so a cell links
 * there when one of its two dimensions is one of those and its key is a real
 * value (not a sentinel).
 */
export function catalogueQuery(
  rowsBy: string,
  row: string,
  colsBy: string,
  col: string,
): Record<string, string> | null {
  const query: Record<string, string> = {}
  for (const [by, key] of [
    [rowsBy, row],
    [colsBy, col],
  ] as const) {
    if (key === UNSET || key === INVALID) {
      continue
    }
    if (by === 'layer') {
      query.layer = key
    } else if (by === 'element_type') {
      query.type = key
    }
  }
  return Object.keys(query).length > 0 ? query : null
}

export type { Layer }
