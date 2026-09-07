// The ArchiMate palette, fetched from the backend.
//
// The SPA must never re-declare the 61 element types: they would drift from
// the metamodel the API validates against. `/metamodel` answers from the same
// code, so a type added there appears here without a frontend change.
import { computed, ref } from 'vue'

import type { components } from '../../api/schema'
import { api, messageOf, unwrap } from '../../lib/api'

export type ElementTypeRead = components['schemas']['ElementTypeRead']
export type ElementType = components['schemas']['ElementType']
export type Layer = components['schemas']['Layer']

export type LayerGroup = { layer: Layer; types: ElementTypeRead[] }

/** Layer names for display; the wire values are snake_case identifiers. */
export const LAYER_LABELS: Record<Layer, string> = {
  motivation: 'Motivation',
  strategy: 'Stratégie',
  business: 'Métier',
  application: 'Application',
  technology: 'Technologie',
  physical: 'Physique',
  implementation_migration: 'Implémentation & migration',
  other: 'Transverse',
}

export function useMetamodel() {
  const elementTypes = ref<ElementTypeRead[]>([])
  const layers = ref<Layer[]>([])
  const error = ref('')

  /** The palette grouped for a `<optgroup>`, in the layer order ArchiMate uses. */
  const byLayer = computed<LayerGroup[]>(() =>
    layers.value
      .map((layer) => ({
        layer,
        types: elementTypes.value.filter((type) => type.layer === layer),
      }))
      .filter((group) => group.types.length > 0),
  )

  const labels = computed(
    () => new Map(elementTypes.value.map((type) => [type.value, type.label])),
  )

  /** The label for a type, or the raw value while the palette is still loading. */
  function labelOf(value: ElementType | string): string {
    return labels.value.get(value as ElementType) ?? value
  }

  async function load(): Promise<void> {
    error.value = ''
    try {
      const metamodel = unwrap(await api.GET('/metamodel', {}))
      elementTypes.value = metamodel.element_types
      layers.value = metamodel.layers
    } catch (caught) {
      // The palette is a convenience: a failure here must not take the whole
      // screen down, so it is reported and the catalogue still lists.
      error.value = messageOf(caught)
    }
  }

  return { elementTypes, layers, byLayer, labelOf, error, load }
}
