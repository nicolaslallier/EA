import { describe, expect, it } from 'vitest'

import { ancestorsOf, buildTree, visibleNodes } from '../src/features/packages/packageTree'
import { aPackage } from './support/api'

const FINANCE = aPackage({ id: 'f', name: 'Finance' })
const BUDGET = aPackage({ id: 'b', name: 'Budget', parent_id: 'f' })
const FORECAST = aPackage({ id: 'p', name: 'Prévisions', parent_id: 'b' })
const ACHATS = aPackage({ id: 'a', name: 'achats' })

describe('buildTree', () => {
  it('nests the flat list by parent and sorts the siblings by name, case aside', () => {
    const tree = buildTree([FORECAST, FINANCE, ACHATS, BUDGET])

    expect(tree.map((node) => node.package.name)).toEqual(['achats', 'Finance'])
    expect(tree[1].children.map((node) => node.package.name)).toEqual(['Budget'])
    expect(tree[1].children[0].children[0].depth).toBe(2)
  })

  it('treats a package whose parent is not in the list as a root rather than losing it', () => {
    const orphan = aPackage({ id: 'o', name: 'Orpheline', parent_id: 'disparu' })

    expect(buildTree([orphan]).map((node) => node.package.id)).toEqual(['o'])
  })

  it('does not loop on a cycle: the packages of a cycle are not reachable from a root', () => {
    const a = aPackage({ id: 'x', name: 'X', parent_id: 'y' })
    const b = aPackage({ id: 'y', name: 'Y', parent_id: 'x' })

    expect(buildTree([a, b])).toEqual([])
  })
})

describe('visibleNodes', () => {
  it('hides the subtree of a collapsed node and shows that of an expanded one', () => {
    const tree = buildTree([FINANCE, BUDGET, FORECAST])

    expect(visibleNodes(tree, new Set()).map((n) => n.package.id)).toEqual(['f'])
    expect(visibleNodes(tree, new Set(['f'])).map((n) => n.package.id)).toEqual(['f', 'b'])
    expect(visibleNodes(tree, new Set(['f', 'b'])).map((n) => n.package.id)).toEqual(['f', 'b', 'p'])
  })
})

describe('ancestorsOf', () => {
  it('lists the ids from the top down to the parent, the package itself left out', () => {
    expect(ancestorsOf('p', [FINANCE, BUDGET, FORECAST])).toEqual(['f', 'b'])
  })

  it('answers nothing for the pseudo-nodes and for an id the list does not know', () => {
    expect(ancestorsOf('', [FINANCE])).toEqual([])
    expect(ancestorsOf('root', [FINANCE])).toEqual([])
    expect(ancestorsOf('disparu', [FINANCE])).toEqual([])
  })
})
