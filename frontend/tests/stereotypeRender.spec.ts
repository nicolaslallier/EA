import { describe, expect, it } from 'vitest'

import { STEREOTYPE_ICONS, stereotypeLabel, stereotypeStroke } from '../src/lib/stereotypeRender'

const ref = (name: string, colour: string | null, icon_key = 'none') => ({
  id: name,
  name,
  colour,
  icon_key,
}) as never

describe('stereotypeRender', () => {
  it('writes the guillemets of every stereotype in order', () => {
    expect(stereotypeLabel([ref('database', null), ref('critique', null)])).toBe('«database» «critique»')
    expect(stereotypeLabel([])).toBe('')
  })

  it('takes the stroke from the first stereotype that has a valid colour', () => {
    expect(stereotypeStroke([ref('a', null), ref('b', '#ff0000'), ref('c', '#00ff00')])).toBe('#ff0000')
    expect(stereotypeStroke([ref('a', 'red" onload="x')])).toBeNull()
    expect(stereotypeStroke([])).toBeNull()
  })

  it('ships an icon for every key of the closed set', () => {
    expect(Object.keys(STEREOTYPE_ICONS).sort()).toEqual(
      ['api', 'cloud', 'database', 'document', 'gear', 'lock', 'none', 'queue', 'server', 'user'],
    )
    expect(STEREOTYPE_ICONS.none).toBe('')
  })
})
