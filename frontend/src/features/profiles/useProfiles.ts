// Profiles and their stereotypes: reference data that extends no ArchiMate type.
//
// A stereotype is a label on an element of one base type, with the properties
// people expect on it. The rules (a dangerous pattern, a reserved key, a
// stereotype in use) are the API's: a refusal is thrown for the screen to show.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'

export type ProfileRead = components['schemas']['ProfileRead']
export type StereotypeRead = components['schemas']['StereotypeRead']
export type StereotypeWrite = components['schemas']['StereotypeWrite']
export type IconKey = components['schemas']['IconKey']

export function useProfiles() {
  const profiles = ref<ProfileRead[]>([])
  const listing = useLatestRequest()
  const { status, error } = listing

  async function load(): Promise<void> {
    await listing.run(
      async (signal) => unwrap(await api.GET('/profiles', { signal })),
      (answer) => {
        profiles.value = answer
      },
    )
  }

  async function createProfile(name: string): Promise<void> {
    unwrap(await api.POST('/profiles', { body: { name, description: '' } }))
    await load()
  }

  async function removeProfile(id: string): Promise<void> {
    unwrap(await api.DELETE('/profiles/{profile_id}', { params: { path: { profile_id: id } } }))
    await load()
  }

  /** Create (`stereotypeId` null) or replace a stereotype, properties included. */
  async function saveStereotype(
    profileId: string,
    stereotypeId: string | null,
    body: StereotypeWrite,
  ): Promise<void> {
    if (stereotypeId === null) {
      unwrap(
        await api.POST('/profiles/{profile_id}/stereotypes', {
          params: { path: { profile_id: profileId } },
          body,
        }),
      )
    } else {
      unwrap(
        await api.PUT('/stereotypes/{stereotype_id}', {
          params: { path: { stereotype_id: stereotypeId } },
          body,
        }),
      )
    }
    await load()
  }

  async function removeStereotype(id: string): Promise<void> {
    unwrap(
      await api.DELETE('/stereotypes/{stereotype_id}', { params: { path: { stereotype_id: id } } }),
    )
    await load()
  }

  /** The profile as the versioned document `importProfile` accepts. */
  async function exportProfile(id: string): Promise<string> {
    const document = unwrap(
      await api.GET('/profiles/{profile_id}/export', { params: { path: { profile_id: id } } }),
    )
    return JSON.stringify(document, null, 2)
  }

  /** Text that is not JSON never reaches the API; everything else is the API's to judge. */
  async function importProfile(text: string): Promise<void> {
    let document: components['schemas']['ProfileDocument']
    try {
      document = JSON.parse(text) as components['schemas']['ProfileDocument']
    } catch {
      throw new Error("Ce fichier n'est pas du JSON valide.")
    }
    unwrap(await api.POST('/profiles/import', { body: document }))
    await load()
  }

  return {
    profiles,
    status,
    error,
    load,
    createProfile,
    removeProfile,
    saveStereotype,
    removeStereotype,
    exportProfile,
    importProfile,
  }
}
