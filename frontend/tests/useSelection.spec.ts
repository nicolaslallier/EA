import { describe, expect, it } from 'vitest'

import { useSelection } from '../src/features/diagrams/useSelection'

describe('useSelection', () => {
  it('toggles an id in and out, keeping the order of arrival', () => {
    const selection = useSelection()

    selection.toggle('a')
    selection.toggle('b')
    selection.toggle('a')

    expect(selection.ids.value).toEqual(['b'])
  })

  it('replaces the whole set without duplicates, and clears', () => {
    const selection = useSelection()

    selection.set(['a', 'b', 'a'])
    expect(selection.ids.value).toEqual(['a', 'b'])

    selection.clear()
    expect(selection.ids.value).toEqual([])
  })
})
