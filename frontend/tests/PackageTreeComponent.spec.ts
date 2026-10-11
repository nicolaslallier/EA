import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import { ELEMENT_DRAG_TYPE } from '../src/features/packages/packageTree'
import PackageTree from '../src/features/packages/PackageTree.vue'
import { aPackage } from './support/api'

const FINANCE = aPackage({ id: 'f', name: 'Finance', child_count: 1, element_count: 2 })
const BUDGET = aPackage({ id: 'b', name: 'Budget', parent_id: 'f', path: 'Finance / Budget' })
const RH = aPackage({ id: 'r', name: 'RH' })

function show(props: Record<string, unknown> = {}) {
  return render(PackageTree, {
    props: { packages: [BUDGET, RH, FINANCE], selected: '', canDrop: true, ...props },
  })
}

const item = (name: RegExp | string) => screen.getByRole('treeitem', { name })

describe('PackageTree', () => {
  it('is a tree with the two pseudo-nodes first and the roots collapsed', () => {
    show()

    expect(screen.getByRole('tree', { name: 'Paquets' })).toBeInTheDocument()
    expect(screen.getAllByRole('treeitem').map((node) => node.textContent?.trim())).toEqual([
      'Tous les éléments',
      'Racine (sans paquet)',
      expect.stringContaining('Finance'),
      expect.stringContaining('RH'),
    ])
    expect(item(/Finance/)).toHaveAttribute('aria-expanded', 'false')
    expect(item(/RH/)).not.toHaveAttribute('aria-expanded')
  })

  it('shows what each package holds directly', () => {
    show()

    expect(item(/Finance/)).toHaveTextContent('2')
  })

  it('selects by click: a package by its id, the unfiled as root, everything as empty', async () => {
    const view = show()

    await fireEvent.click(item(/RH/))
    await fireEvent.click(item(/Racine/))
    await fireEvent.click(item(/Tous/))

    expect(view.emitted().select).toEqual([['r'], ['root'], ['']])
  })

  it('expands with the right arrow, collapses with the left one, and moves with up and down', async () => {
    show()
    const finance = item(/Finance/)

    await fireEvent.keyDown(finance, { key: 'ArrowRight' })
    expect(finance).toHaveAttribute('aria-expanded', 'true')
    expect(item(/Budget/)).toHaveAttribute('aria-level', '2')

    await fireEvent.keyDown(finance, { key: 'ArrowRight' })
    await waitFor(() => expect(item(/Budget/)).toHaveFocus())

    await fireEvent.keyDown(item(/Budget/), { key: 'ArrowLeft' })
    await waitFor(() => expect(finance).toHaveFocus())
    await fireEvent.keyDown(finance, { key: 'ArrowLeft' })
    expect(finance).toHaveAttribute('aria-expanded', 'false')

    await fireEvent.keyDown(finance, { key: 'ArrowDown' })
    await waitFor(() => expect(item(/RH/)).toHaveFocus())
  })

  it('selects with Enter', async () => {
    const view = show()

    await fireEvent.keyDown(item(/RH/), { key: 'Enter' })

    expect(view.emitted().select).toEqual([['r']])
  })

  it('opens the ancestors of the selected package, and marks only that one selected', () => {
    show({ selected: 'b' })

    expect(item(/Budget/)).toHaveAttribute('aria-selected', 'true')
    expect(item(/Finance/)).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getAllByRole('treeitem', { selected: true })).toHaveLength(1)
  })

  it('selects nothing when the URL names a package that is gone', () => {
    show({ selected: 'disparu' })

    expect(screen.queryAllByRole('treeitem', { selected: true })).toHaveLength(0)
  })

  it('emits a drop on a package or on the root, and ignores one on "all"', async () => {
    const view = show()
    const dataTransfer = { getData: () => 'e1', types: [ELEMENT_DRAG_TYPE] }

    await fireEvent.drop(item(/RH/), { dataTransfer })
    await fireEvent.drop(item(/Racine/), { dataTransfer })
    await fireEvent.drop(item(/Tous/), { dataTransfer })

    expect(view.emitted().drop).toEqual([
      ['e1', 'r'],
      ['e1', 'root'],
    ])
  })

  it('accepts no drop at all from a reader', async () => {
    const view = show({ canDrop: false })

    await fireEvent.drop(item(/RH/), {
      dataTransfer: { getData: () => 'e1', types: [ELEMENT_DRAG_TYPE] },
    })

    expect(view.emitted().drop).toBeUndefined()
  })
})
