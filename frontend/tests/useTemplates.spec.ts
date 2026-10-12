import { afterEach, describe, expect, it, vi } from 'vitest'

import { useTemplates } from '../src/features/diagrams/useTemplates'
import { stubApi } from './support/api'

afterEach(() => vi.unstubAllGlobals())

const SUMMARY = {
  id: 't1',
  name: 'Application + hôte',
  description: '',
  slot_count: 2,
  group_count: 1,
  note_count: 1,
  link_count: 1,
  created_at: '2026-10-10T12:00:00Z',
  updated_at: '2026-10-10T12:00:00Z',
}

describe('useTemplates', () => {
  it('loads the library', async () => {
    stubApi([{ path: '/diagram-templates', body: [SUMMARY] }])
    const templates = useTemplates()

    await templates.load()

    expect(templates.templates.value.map((t) => t.name)).toEqual(['Application + hôte'])
  })

  it('saves a diagram as a template and reloads the library', async () => {
    const calls = stubApi([
      { method: 'POST', path: '/diagrams/d1/as-template', status: 201, body: { ...SUMMARY, body: {} } },
      { path: '/diagram-templates', body: [SUMMARY] },
    ])
    const templates = useTemplates()

    await templates.saveFromDiagram('d1', 'Modèle')

    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ name: 'Modèle', description: '' })
    expect(templates.templates.value).toHaveLength(1)
  })

  it('instantiates and hands back the diagram, relationships off unless asked', async () => {
    const calls = stubApi([
      { method: 'POST', path: '/diagram-templates/t1/instantiate', status: 201, body: { id: 'd2' } },
    ])
    const templates = useTemplates()

    const created = await templates.instantiate('t1', {
      name: 'Facturation',
      bindings: [{ slot_key: 'app', element_id: null, create: { name: 'Facturation' } }],
      create_relationships: false,
    })

    expect(created.id).toBe('d2')
    expect((calls[0].body as { create_relationships: boolean }).create_relationships).toBe(false)
  })

  it('throws the refusal of a binding of the wrong type, for the wizard to show', async () => {
    stubApi([
      {
        method: 'POST',
        path: '/diagram-templates/t1/instantiate',
        status: 422,
        body: { error: 'invalid_input', detail: "slot 'app' wants a application_component, got node" },
      },
    ])
    const templates = useTemplates()

    await expect(
      templates.instantiate('t1', { name: 'X', bindings: [], create_relationships: false }),
    ).rejects.toThrow(/wants a application_component/)
  })
})
