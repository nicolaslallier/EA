import { describe, expect, it } from 'vitest'

import {
  MAX_GROUP_DEPTH,
  canNest,
  descendantsOf,
  groupAt,
  groupHeight,
  groupsAbove,
  moveWithChildren,
  removeGroup,
  type SceneParts,
} from '../src/lib/sceneObjects'

type Obj = SceneParts['objects'][number]

const group = (id: string, parent: string | null = null, extra: Partial<Obj> = {}): Obj => ({
  id,
  kind: 'group',
  x: 0,
  y: 0,
  width: 400,
  height: 300,
  text: id,
  colour: null,
  z: 0,
  parent_id: parent,
  anchor_element_id: null,
  ...extra,
})
const note = (id: string, parent: string | null = null): Obj =>
  group(id, parent, { kind: 'note', width: 100, height: 50 })
const box = (element_id: string, parent: string | null, x = 0, y = 0) => ({
  element_id,
  x,
  y,
  width: 100,
  height: 40,
  parent_object_id: parent,
  z: 0,
})

/** g1 > g2 > g3 > g4 > g5, the deepest the API allows. */
const chain = (length: number) =>
  Array.from({ length }, (_, i) => group(`g${i + 1}`, i === 0 ? null : `g${i}`))

describe('nesting', () => {
  it('counts the groups above an object and below a group', () => {
    const objects = chain(3)

    expect(groupsAbove(objects, 'g3')).toBe(2)
    expect(groupHeight(objects, 'g1')).toBe(2)
    expect(groupHeight(objects, 'g3')).toBe(0)
  })

  it('collects every descendant, however deep', () => {
    const objects = [...chain(3), note('n', 'g2')]

    expect(descendantsOf(objects, 'g1')).toEqual(new Set(['g2', 'g3', 'n']))
  })

  it('refuses to put a group into itself or into one of its own descendants', () => {
    const objects = chain(3)

    expect(canNest(objects, 'g1', true, 'g1')).toBe(false)
    expect(canNest(objects, 'g1', true, 'g3')).toBe(false)
    expect(canNest(objects, 'g3', true, 'g1')).toBe(true)
  })

  it('refuses a sixth level of groups but welcomes a note or a box in the fifth', () => {
    const objects = chain(MAX_GROUP_DEPTH)

    expect(canNest(objects, null, true, 'g5')).toBe(false)
    expect(canNest(objects, null, false, 'g5')).toBe(true)
  })

  it('counts the depth of the whole subtree being moved, not just its root', () => {
    const objects = [...chain(2), group('a'), group('b', 'a'), group('c', 'b')]

    // g2 sits at level 2; moving the 3-deep tree `a` under it would make 5, then 6.
    expect(canNest(objects, 'a', true, 'g2')).toBe(true)
    expect(canNest([...objects, group('d', 'c')], 'a', true, 'g2')).toBe(false)
  })

  it('refuses a parent that is a note or does not exist', () => {
    expect(canNest([note('n')], null, false, 'n')).toBe(false)
    expect(canNest([], null, false, 'ghost')).toBe(false)
  })
})

describe('groupAt', () => {
  const objects = [
    group('outer', null, { x: 0, y: 0, width: 500, height: 400 }),
    group('inner', 'outer', { x: 50, y: 50, width: 200, height: 150 }),
  ]

  it('picks the deepest group holding the point', () => {
    expect(groupAt(objects, { x: 100, y: 100 }, { id: null, isGroup: false })).toBe('inner')
    expect(groupAt(objects, { x: 400, y: 350 }, { id: null, isGroup: false })).toBe('outer')
  })

  it('is null outside every group', () => {
    expect(groupAt(objects, { x: 900, y: 900 }, { id: null, isGroup: false })).toBeNull()
  })

  it('never picks the group being moved, nor one of its descendants', () => {
    expect(groupAt(objects, { x: 100, y: 100 }, { id: 'outer', isGroup: true })).toBeNull()
    expect(groupAt(objects, { x: 100, y: 100 }, { id: 'inner', isGroup: true })).toBe('outer')
  })
})

describe('moveWithChildren', () => {
  it('moves the group, its sub-groups, notes and boxes — and nothing else', () => {
    const parts: SceneParts = {
      objects: [
        group('g', null, { x: 10, y: 10 }),
        group('sub', 'g', { x: 20, y: 20 }),
        note('n', 'sub'),
        group('other', null, { x: 500, y: 500 }),
      ],
      nodes: [box('in', 'sub', 30, 30), box('out', null, 600, 600)],
    }

    const moved = moveWithChildren(parts, 'g', 5, -5)

    const at = (id: string) => moved.objects.find((o) => o.id === id)!
    expect([at('g').x, at('g').y]).toEqual([15, 5])
    expect([at('sub').x, at('sub').y]).toEqual([25, 15])
    expect([at('other').x, at('other').y]).toEqual([500, 500])
    expect(moved.nodes.find((n) => n.element_id === 'in')).toMatchObject({ x: 35, y: 25 })
    expect(moved.nodes.find((n) => n.element_id === 'out')).toMatchObject({ x: 600, y: 600 })
  })
})

describe('removeGroup', () => {
  it('lifts boxes, notes and sub-groups to the parent of the group', () => {
    const parts: SceneParts = {
      objects: [group('outer'), group('inner', 'outer'), note('n', 'inner'), group('deep', 'inner')],
      nodes: [box('e', 'inner')],
    }

    const result = removeGroup(parts, 'inner')

    expect(result.objects.map((o) => o.id)).toEqual(['outer', 'n', 'deep'])
    expect(result.objects.filter((o) => o.id !== 'outer').map((o) => o.parent_id)).toEqual([
      'outer',
      'outer',
    ])
    expect(result.nodes[0].parent_object_id).toBe('outer')
  })

  it('frees the children of a top-level group', () => {
    const parts: SceneParts = { objects: [group('g'), note('n', 'g')], nodes: [box('e', 'g')] }

    const result = removeGroup(parts, 'g')

    expect(result.objects[0].parent_id).toBeNull()
    expect(result.nodes[0].parent_object_id).toBeNull()
  })

  it('removes an empty group and leaves a missing id alone', () => {
    const parts: SceneParts = { objects: [group('g')], nodes: [] }

    expect(removeGroup(parts, 'g').objects).toEqual([])
    expect(removeGroup(parts, 'ghost')).toEqual(parts)
  })
})
