import { describe, expect, it } from 'vitest'

import { newId } from '../src/lib/newId'

describe('newId', () => {
  it('is a version-4 UUID and never the same twice', () => {
    const [a, b] = [newId(), newId()]

    expect(a).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}$/)
    expect(a).not.toBe(b)
  })
})
