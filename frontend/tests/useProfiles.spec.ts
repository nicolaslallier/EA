import { afterEach, describe, expect, it, vi } from 'vitest'

import { useProfiles } from '../src/features/profiles/useProfiles'
import { stubApi } from './support/api'

afterEach(() => vi.unstubAllGlobals())

const PROFILE = {
  id: 'pppppppp-0000-4000-8000-000000000000',
  name: 'SI',
  description: '',
  created_at: '2026-10-10T12:00:00Z',
  updated_at: '2026-10-10T12:00:00Z',
  stereotypes: [],
}

describe('useProfiles', () => {
  it('loads the profiles', async () => {
    stubApi([{ path: '/profiles', body: [PROFILE] }])
    const profiles = useProfiles()

    await profiles.load()

    expect(profiles.profiles.value.map((p) => p.name)).toEqual(['SI'])
    expect(profiles.status.value).toBe('ready')
  })

  it('creates a profile then reloads the list', async () => {
    const calls = stubApi([
      { method: 'POST', path: '/profiles', status: 201, body: PROFILE },
      { path: '/profiles', body: [PROFILE] },
    ])
    const profiles = useProfiles()

    await profiles.createProfile('SI')

    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ name: 'SI', description: '' })
    expect(profiles.profiles.value).toHaveLength(1)
  })

  it('throws the refusal of a stereotype in use, with its count, for the screen to show', async () => {
    stubApi([
      {
        method: 'DELETE',
        path: '/stereotypes/s1',
        status: 409,
        body: { error: 'stereotype_in_use', detail: 'applied to 3 element(s)' },
      },
    ])
    const profiles = useProfiles()

    await expect(profiles.removeStereotype('s1')).rejects.toThrow('applied to 3 element(s)')
  })

  it('exports as text and imports from text, refusing invalid JSON before any request', async () => {
    const calls = stubApi([
      { path: `/profiles/${PROFILE.id}/export`, body: { profile_format: 1, name: 'SI', description: '', stereotypes: [] } },
      { method: 'POST', path: '/profiles/import', status: 201, body: PROFILE },
      { path: '/profiles', body: [PROFILE] },
    ])
    const profiles = useProfiles()

    expect(JSON.parse(await profiles.exportProfile(PROFILE.id)).profile_format).toBe(1)
    await expect(profiles.importProfile('{not json')).rejects.toThrow(/JSON/)
    expect(calls.some((c) => c.method === 'POST')).toBe(false)
    await profiles.importProfile(JSON.stringify({ profile_format: 1, name: 'SI', description: '', stereotypes: [] }))
    expect(calls.some((c) => c.method === 'POST')).toBe(true)
  })
})
