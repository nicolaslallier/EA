import { describe, expect, it } from 'vitest'

import {
  MAX_URL_QUERY,
  cloneQuery,
  decodeQuery,
  emptyQuery,
  encodeQuery,
} from '../src/features/search/queryState'

describe('emptyQuery', () => {
  it('is a version-1 query that filters nothing, with every field the API requires', () => {
    const query = emptyQuery()

    expect(query.version).toBe(1)
    expect(Object.keys(query).sort()).toEqual(
      [
        'element_types', 'include_subpackages', 'layers', 'limit', 'offset', 'order',
        'package_id', 'properties', 'relations', 'root_only', 'text', 'version',
      ].sort(),
    ) // fmt: skip
    expect(query.limit).toBe(25)
  })

  it('is a new object each time, so editing one never edits another', () => {
    const one = emptyQuery()
    one.layers.push('business')

    expect(emptyQuery().layers).toEqual([])
  })
})

describe('the URL form', () => {
  it('round-trips a query, the page left out', () => {
    const query = { ...emptyQuery(), text: 'facturation', offset: 50 }

    const raw = encodeQuery(query)

    expect(raw).not.toBeNull()
    expect(decodeQuery(raw as string)).toEqual({ ...query, offset: 0 })
  })

  it('refuses to put more than the limit in an address, and says so with null', () => {
    const long = { ...emptyQuery(), text: 'x'.repeat(MAX_URL_QUERY) }

    expect(encodeQuery(long)).toBeNull()
  })

  it('completes a hand-written short query with the defaults', () => {
    const query = decodeQuery('{"version":1,"text":"db"}')

    expect(query).toEqual({ ...emptyQuery(), text: 'db' })
  })

  it.each(['', 'pas du json', '3', 'null', '[1]', '{"version":2}', '{"text":"db"}'])(
    'answers null for %j rather than throwing',
    (raw) => {
      expect(decodeQuery(raw)).toBeNull()
    },
  )

  it('falls back to the default for a list field that is not a list, so the form never breaks on a hand-written link', () => {
    const query = decodeQuery('{"version":1,"layers":"business","properties":null,"relations":3,"element_types":{}}')

    expect(query).toEqual(emptyQuery())
  })

  it('clones without sharing', () => {
    const query = emptyQuery()
    const copy = cloneQuery(query)
    copy.layers.push('business')

    expect(query.layers).toEqual([])
  })
})
