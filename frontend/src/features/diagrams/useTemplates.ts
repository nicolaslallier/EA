// The library of diagram templates, and the two ways to use it: save a diagram
// as one, and build a diagram from one. A refusal (a slot bound to an element
// of another type, say) is thrown for the wizard to show.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'

export type DiagramTemplateSummaryRead = components['schemas']['DiagramTemplateSummaryRead']
export type DiagramTemplateRead = components['schemas']['DiagramTemplateRead']
export type InstantiateRequest = components['schemas']['InstantiateRequest']
export type DiagramRead = components['schemas']['DiagramRead']

export function useTemplates() {
  const templates = ref<DiagramTemplateSummaryRead[]>([])
  const listing = useLatestRequest()
  const { status, error } = listing

  async function load(): Promise<void> {
    await listing.run(
      async (signal) => unwrap(await api.GET('/diagram-templates', { signal })),
      (answer) => {
        templates.value = answer
      },
    )
  }

  async function read(id: string): Promise<DiagramTemplateRead> {
    return unwrap(
      await api.GET('/diagram-templates/{template_id}', { params: { path: { template_id: id } } }),
    )
  }

  async function remove(id: string): Promise<void> {
    unwrap(
      await api.DELETE('/diagram-templates/{template_id}', {
        params: { path: { template_id: id } },
      }),
    )
    await load()
  }

  async function saveFromDiagram(
    diagramId: string,
    name: string,
    description = '',
  ): Promise<DiagramTemplateRead> {
    const created = unwrap(
      await api.POST('/diagrams/{diagram_id}/as-template', {
        params: { path: { diagram_id: diagramId } },
        body: { name, description },
      }),
    )
    await load()
    return created
  }

  async function instantiate(id: string, payload: InstantiateRequest): Promise<DiagramRead> {
    return unwrap(
      await api.POST('/diagram-templates/{template_id}/instantiate', {
        params: { path: { template_id: id } },
        body: payload,
      }),
    )
  }

  return { templates, status, error, load, read, remove, saveFromDiagram, instantiate }
}
