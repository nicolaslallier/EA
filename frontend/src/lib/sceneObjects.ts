
// Where things sit inside a group, and what happens to them when it moves or goes.
//
// A group is "drawn together" and nothing more (docs/adr/0069): it
// is not an element and creates no relationship, so removing one never touches
// the graph. These functions only edit the *scene* — boxes and objects — and
// never mutate their input. The depth limit below mirrors the API's
// `MAX_GROUP_DEPTH`; it spares the user a refusal, it does not replace it.
import type { components } from '../api/schema'
import type { Point } from './diagramGeometry'

type SceneObject = components['schemas']['DiagramObject']
type SceneNode = components['schemas']['DiagramNode']
export type SceneParts = { nodes: SceneNode[]; objects: SceneObject[] }

/** Groups that may contain one another; a note or a box may sit in the deepest. */
export const MAX_GROUP_DEPTH = 5

const indexed = (objects: SceneObject[]) => new Map(objects.map((item) => [item.id, item]))

/** Every object, however deep, under `groupId`. */
export function descendantsOf(objects: SceneObject[], groupId: string): Set<string> {
  const found = new Set<string>()
  const queue = [groupId]
  while (queue.length > 0) {
    const parent = queue.pop()
    for (const item of objects) {
      if (item.parent_id === parent && !found.has(item.id)) {
        found.add(item.id)
        queue.push(item.id)
      }
    }
  }
  return found
}

/** How many groups lie above `id`. A loop, which the API refuses anyway, stops the count. */
export function groupsAbove(objects: SceneObject[], id: string): number {
  const byId = indexed(objects)
  const seen = new Set<string>()
  let depth = 0
  let current = byId.get(id)?.parent_id ?? null
  while (current !== null && !seen.has(current)) {
    seen.add(current)
    depth += 1
    current = byId.get(current)?.parent_id ?? null
  }
  return depth
}

/** How many levels of groups lie strictly below group `id`. */
export function groupHeight(objects: SceneObject[], id: string): number {
  const children = objects.filter((item) => item.parent_id === id && item.kind === 'group')
  return children.length === 0 ? 0 : 1 + Math.max(...children.map((c) => groupHeight(objects, c.id)))
}

/**
 * Whether an object (`childId`, null for a box, which has no object id) may be
 * put inside `parentId` without a loop and within the depth limit.
 */
export function canNest(
  objects: SceneObject[],
  childId: string | null,
  childIsGroup: boolean,
  parentId: string,
): boolean {
  const parent = objects.find((item) => item.id === parentId)
  if (!parent || parent.kind !== 'group') {
    return false
  }
  if (childId !== null && (parentId === childId || descendantsOf(objects, childId).has(parentId))) {
    return false
  }
  const parentLevel = groupsAbove(objects, parentId) + 1
  if (!childIsGroup) {
    return parentLevel <= MAX_GROUP_DEPTH
  }
  const below = childId === null ? 0 : groupHeight(objects, childId)
  return parentLevel + 1 + below <= MAX_GROUP_DEPTH
}

/** The deepest group that holds `point` and may take the child, or null. */
export function groupAt(
  objects: SceneObject[],
  point: Point,
  child: { id: string | null; isGroup: boolean },
): string | null {
  const candidates = objects
    .filter(
      (item) =>
        item.kind === 'group' &&
        point.x >= item.x &&
        point.x <= item.x + item.width &&
        point.y >= item.y &&
        point.y <= item.y + item.height &&
        canNest(objects, child.id, child.isGroup, item.id),
    )
    .map((item) => ({ item, depth: groupsAbove(objects, item.id), area: item.width * item.height }))
    .sort((a, b) => b.depth - a.depth || a.area - b.area || a.item.id.localeCompare(b.item.id))
  return candidates[0]?.item.id ?? null
}

/** Translate a group, everything inside it at any depth, and nothing else. */
export function moveWithChildren(
  parts: SceneParts,
  groupId: string,
  dx: number,
  dy: number,
): SceneParts {
  const moving = descendantsOf(parts.objects, groupId)
  moving.add(groupId)
  return {
    objects: parts.objects.map((item) =>
      moving.has(item.id) ? { ...item, x: item.x + dx, y: item.y + dy } : item,
    ),
    nodes: parts.nodes.map((node) =>
      node.parent_object_id !== null && moving.has(node.parent_object_id)
        ? { ...node, x: node.x + dx, y: node.y + dy }
        : node,
    ),
  }
}

/**
 * Take a group away, lifting what it held to the group's own parent. No
 * element is deleted — a box leaves the group, not the diagram.
 */
export function removeGroup(parts: SceneParts, groupId: string): SceneParts {
  const group = parts.objects.find((item) => item.id === groupId)
  if (!group) {
    return parts
  }
  return {
    objects: parts.objects
      .filter((item) => item.id !== groupId)
      .map((item) => (item.parent_id === groupId ? { ...item, parent_id: group.parent_id } : item)),
    nodes: parts.nodes.map((node) =>
      node.parent_object_id === groupId ? { ...node, parent_object_id: group.parent_id } : node,
    ),
  }
}
