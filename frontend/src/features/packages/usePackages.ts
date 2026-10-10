// The classification tree: the flat list, the tree built from it, and the writes.
//
// The tree is served in one piece (at most 2 000 packages) and counts what each
// package holds directly, so every write reloads: a count is the server's to
// compute, not ours to patch up after a move.
import { computed, ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import { buildTree, type PackageRead } from './packageTree'

export type PackageCreate = components['schemas']['PackageCreate']
export type PackageUpdate = components['schemas']['PackageUpdate']

export function usePackages() {
  const packages = ref<PackageRead[]>([])
  // The tree is reloaded after every write and by every screen that shows it:
  // the answer on screen is the one to the last question.
  const listing = useLatestRequest()
  const { status, error } = listing

  const tree = computed(() => buildTree(packages.value))
  const byId = computed(() => new Map(packages.value.map((entry) => [entry.id, entry])))

  async function load(): Promise<void> {
    await listing.run(
      async (signal) => unwrap(await api.GET('/packages', { signal })),
      (answer) => {
        packages.value = answer
      },
    )
  }

  /** A refusal — a name already taken, a tree too deep — is thrown, for the form to show. */
  async function create(body: PackageCreate): Promise<PackageRead> {
    const created = unwrap(await api.POST('/packages', { body }))
    await load()
    return created
  }

  /** `parent_id: null` puts the package at the root; leaving it out keeps the parent. */
  async function update(id: string, body: PackageUpdate): Promise<PackageRead> {
    const updated = unwrap(
      await api.PATCH('/packages/{package_id}', { params: { path: { package_id: id } }, body }),
    )
    await load()
    return updated
  }

  /** Without `moveContentsTo` a package that holds anything is refused (409). */
  async function remove(id: string, moveContentsTo?: string): Promise<void> {
    unwrap(
      await api.DELETE('/packages/{package_id}', {
        params: {
          path: { package_id: id },
          query: moveContentsTo === undefined ? {} : { move_contents_to: moveContentsTo },
        },
      }),
    )
    await load()
  }

  /** `packageId: null` files the elements at the root. */
  async function moveElements(elementIds: string[], packageId: string | null): Promise<void> {
    unwrap(
      await api.POST('/elements/move', {
        body: { element_ids: elementIds, package_id: packageId },
      }),
    )
    await load()
  }

  return { packages, byId, tree, status, error, load, create, update, remove, moveElements }
}
