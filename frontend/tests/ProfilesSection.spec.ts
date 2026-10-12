import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'

import ProfilesSection from '../src/features/profiles/ProfilesSection.vue'
import { useMe } from '../src/lib/me'
import { METAMODEL, stubApi, type Route } from './support/api'

afterEach(() => vi.unstubAllGlobals())

const STEREOTYPE = {
  id: 'ssssssss-0000-4000-8000-000000000000',
  profile_id: 'pppppppp-0000-4000-8000-000000000000',
  name: 'database',
  base_type: 'application_component',
  colour: '#ffe0e0',
  icon_key: 'database',
  properties: [
    { key: 'tier', label: 'Niveau', required: true, kind: 'enum', allowed_values: ['1', '2'], template: null, help: '' },
  ],
}
const PROFILE = {
  id: 'pppppppp-0000-4000-8000-000000000000',
  name: 'SI',
  description: 'Profil du SI',
  created_at: '2026-10-10T12:00:00Z',
  updated_at: '2026-10-10T12:00:00Z',
  stereotypes: [STEREOTYPE],
}
const ROUTES: Route[] = [
  { path: '/profiles', body: [PROFILE] },
  { path: '/metamodel', body: METAMODEL },
]

function asAReader(): void {
  vi.mocked(useMe).mockReturnValueOnce({
    me: ref({ username: 'reader', can_write: false, can_approve: false }),
    canWrite: computed(() => false),
    error: ref(null),
    load: vi.fn(() => Promise.resolve()),
  })
}

describe('ProfilesSection', () => {
  it('lists the profiles with their stereotypes and the properties they expect', async () => {
    stubApi(ROUTES)
    render(ProfilesSection)

    expect(await screen.findByRole('heading', { name: 'SI' })).toBeInTheDocument()
    const stereotype = screen.getByRole('listitem', { name: /database/ })
    expect(within(stereotype).getByText(/Niveau/)).toBeInTheDocument()
    expect(within(stereotype).getByText(/obligatoire/i)).toBeInTheDocument()
  })

  it('creates a profile by name', async () => {
    const calls = stubApi([{ method: 'POST', path: '/profiles', status: 201, body: PROFILE }, ...ROUTES])
    render(ProfilesSection)
    await screen.findByRole('heading', { name: 'SI' })

    await fireEvent.update(screen.getByLabelText('Nom du profil'), 'Sécurité')
    await fireEvent.click(screen.getByRole('button', { name: 'Créer le profil' }))

    await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ name: 'Sécurité', description: '' }))
  })

  it('shows why a stereotype in use cannot be deleted', async () => {
    stubApi([
      { method: 'DELETE', path: `/stereotypes/${STEREOTYPE.id}`, status: 409,
        body: { error: 'stereotype_in_use', detail: 'The stereotype is applied to 3 element(s)' } },
      ...ROUTES,
    ])
    render(ProfilesSection)
    await screen.findByRole('heading', { name: 'SI' })

    await fireEvent.click(screen.getByRole('button', { name: 'Supprimer le stéréotype database' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('3 element(s)')
  })

  it('saves a new stereotype with a closed list of icons and the base types of the metamodel', async () => {
    const calls = stubApi([
      { method: 'POST', path: `/profiles/${PROFILE.id}/stereotypes`, status: 201, body: STEREOTYPE },
      ...ROUTES,
    ])
    render(ProfilesSection)
    await screen.findByRole('heading', { name: 'SI' })

    await fireEvent.click(screen.getByRole('button', { name: 'Ajouter un stéréotype à SI' }))
    await fireEvent.update(screen.getByLabelText('Nom du stéréotype'), 'queue')
    await fireEvent.update(screen.getByLabelText('Type de base'), 'application_component')
    await fireEvent.update(screen.getByLabelText('Icône'), 'queue')
    await fireEvent.click(screen.getByRole('button', { name: 'Enregistrer le stéréotype' }))

    await waitFor(() =>
      expect(calls.find((c) => c.method === 'POST' && c.url.pathname.endsWith('/stereotypes'))?.body).toMatchObject({
        name: 'queue',
        base_type: 'application_component',
        icon_key: 'queue',
        properties: [],
      }),
    )
  })

  it('offers a reader the export and nothing that writes', async () => {
    stubApi(ROUTES)
    asAReader()
    render(ProfilesSection)
    await screen.findByRole('heading', { name: 'SI' })

    expect(screen.getByRole('button', { name: 'Exporter SI' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Nom du profil')).toBeNull()
    expect(screen.queryByLabelText('Importer un profil')).toBeNull()
    expect(screen.queryByRole('button', { name: /Supprimer/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Ajouter un stéréotype/ })).toBeNull()
  })
})
