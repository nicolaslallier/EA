import { describe, expect, it } from 'vitest'

import { sha256Of } from '../src/features/exchanges/digest'

describe('sha256Of', () => {
  it('is what shasum -a 256 prints for one file', async () => {
    expect(await sha256Of([new File(['abc'], 'a.xml')])).toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    )
  })

  it('tells two parts from their concatenation', async () => {
    const split = await sha256Of([new File(['ab'], 'e.csv'), new File(['c'], 'r.csv')])
    expect(split).not.toBe(await sha256Of([new File(['abc'], 'e.csv')]))
  })
})
