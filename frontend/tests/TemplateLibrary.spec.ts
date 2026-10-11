import { fireEvent, render, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import TemplateLibrary from '../src/features/diagrams/TemplateLibrary.vue'

const TEMPLATES = [
  {
    id: 't1',
    name: 'Application + hôte',
    description: 'Le classique',
    slot_count: 2,
    group_count: 1,
    note_count: 1,
    link_count: 1,
    created_at: '2026-10-10T12:00:00Z',
    updated_at: '2026-10-10T12:00:00Z',
  },
]

describe('TemplateLibrary', () => {
  it('lists the templates with what they hold and emits the one to use', async () => {
    const rendered = render(TemplateLibrary, { props: { templates: TEMPLATES, canWrite: true } })

    expect(screen.getByText(/2 emplacements/)).toBeInTheDocument()
    await fireEvent.click(screen.getByRole('button', { name: 'Utiliser Application + hôte' }))

    expect(rendered.emitted().use).toEqual([['t1']])
  })

  it('offers a reader the list alone, never the use nor the deletion', () => {
    render(TemplateLibrary, { props: { templates: TEMPLATES, canWrite: false } })

    expect(screen.getByText('Application + hôte')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Supprimer/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Utiliser/ })).toBeNull()
  })
})
