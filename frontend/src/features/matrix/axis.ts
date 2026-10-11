// What an axis of the matrix is, as the URL spells it and as the API wants it.
//
// A choice is one of: the whole catalogue, one layer, one element type, or a
// list of named elements (which other screens will hand over by link). The
// values are passed through as typed — whether `layer:business` names a real
// layer is the API's to say, never a list kept here (docs/adr/0012).
import type { components } from '../../api/schema'

export type AxisSelect = components['schemas']['AxisSelect']
type Layer = components['schemas']['Layer']
type ElementType = components['schemas']['ElementType']

export type AxisChoice =
  | { kind: 'all' }
  | { kind: 'layer'; layer: Layer }
  | { kind: 'type'; type: ElementType }
  | { kind: 'ids'; ids: string[] }
  | { kind: 'package'; id: string }
  | { kind: 'query'; id: string }

export function parseAxis(text: string | undefined): AxisChoice {
  if (!text) {
    return { kind: 'all' }
  }
  const split = text.indexOf(':')
  const head = split < 0 ? text : text.slice(0, split)
  const rest = split < 0 ? '' : text.slice(split + 1)
  if (head === 'layer' && rest) {
    return { kind: 'layer', layer: rest as Layer }
  }
  if (head === 'type' && rest) {
    return { kind: 'type', type: rest as ElementType }
  }
  if (head === 'pkg' && rest) {
    return { kind: 'package', id: rest }
  }
  if (head === 'q' && rest) {
    return { kind: 'query', id: rest }
  }
  if (head === 'ids') {
    const ids = rest.split(',').filter(Boolean)
    return ids.length > 0 ? { kind: 'ids', ids } : { kind: 'all' }
  }
  return { kind: 'all' }
}

export function axisToParam(choice: AxisChoice): string {
  switch (choice.kind) {
    case 'all':
      return 'all'
    case 'layer':
      return `layer:${choice.layer}`
    case 'type':
      return `type:${choice.type}`
    case 'ids':
      return `ids:${choice.ids.join(',')}`
    case 'package':
      return `pkg:${choice.id}`
    case 'query':
      return `q:${choice.id}`
  }
}

export function axisToBody(choice: AxisChoice): AxisSelect {
  switch (choice.kind) {
    case 'all':
      return {}
    case 'layer':
      return { layers: [choice.layer] }
    case 'type':
      return { element_types: [choice.type] }
    case 'ids':
      return { element_ids: choice.ids }
    case 'package':
      return { package_id: choice.id }
    case 'query':
      return { query_id: choice.id }
  }
}
