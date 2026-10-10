// The classification tree, built from the flat list `GET /packages` serves.
//
// The API gives each package its parent and nothing more; the nesting is the
// SPA's job, and it is pure so it is tested without mounting anything. No
// rule lives here — a cycle, a depth, a duplicate name are the API's to
// refuse; this only has to survive whatever it is handed.
import type { components } from '../../api/schema'

export type PackageRead = components['schemas']['PackageRead']

export type TreeNode = { package: PackageRead; depth: number; children: TreeNode[] }

/** The three things a node of the tree can select: no filter, the unfiled, one package. */
export const ALL = ''
export const ROOT = 'root'

/** What a dragged catalogue row carries, and what a node of the tree accepts. */
export const ELEMENT_DRAG_TYPE = 'application/x-ea-element'

function byName(one: PackageRead, other: PackageRead): number {
  return one.name.localeCompare(other.name, 'fr', { sensitivity: 'base' })
}

export function buildTree(flat: readonly PackageRead[]): TreeNode[] {
  const known = new Set(flat.map((entry) => entry.id))
  const byParent = new Map<string | null, PackageRead[]>()
  for (const entry of flat) {
    // A parent the list does not hold makes a root, so nothing is lost.
    const key = entry.parent_id !== null && known.has(entry.parent_id) ? entry.parent_id : null
    byParent.set(key, [...(byParent.get(key) ?? []), entry])
  }
  const build = (parent: string | null, depth: number): TreeNode[] =>
    [...(byParent.get(parent) ?? [])].sort(byName).map((entry) => ({
      package: entry,
      depth,
      children: build(entry.id, depth + 1),
    }))
  return build(null, 0)
}

/** The nodes a reader can see, in tree order: a collapsed node hides its subtree. */
export function visibleNodes(tree: readonly TreeNode[], expanded: ReadonlySet<string>): TreeNode[] {
  return tree.flatMap((node) => [
    node,
    ...(expanded.has(node.package.id) ? visibleNodes(node.children, expanded) : []),
  ])
}

/** The ids on the way from the top down to the parent of `id`, `id` itself left out. */
export function ancestorsOf(id: string, flat: readonly PackageRead[]): string[] {
  const byId = new Map(flat.map((entry) => [entry.id, entry]))
  const path: string[] = []
  let current = byId.get(id)
  // Bounded by the list's own size: a cycle must not hang the screen.
  for (let step = 0; current?.parent_id && step < flat.length; step += 1) {
    path.unshift(current.parent_id)
    current = byId.get(current.parent_id)
  }
  return path
}
