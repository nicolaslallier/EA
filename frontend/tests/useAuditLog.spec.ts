import { afterEach, describe, expect, it, vi } from 'vitest'

import { useAuditLog } from '../src/features/history/useAuditLog'
import { anAuditEvent, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useAuditLog', () => {
  it('sends the filters under the names the backend reads, and drops the empty ones', async () => {
    const calls = stubApi([
      { path: '/audit', body: { items: [anAuditEvent()], next_cursor: 'next' } },
    ])
    const log = useAuditLog()

    await log.load({ kind: 'element', entity: 'abc', actor: '', cursor: 'c1' }, 20)

    const query = calls[0]?.url.searchParams
    expect(query?.get('entity_kind')).toBe('element')
    expect(query?.get('entity_id')).toBe('abc')
    expect(query?.get('cursor')).toBe('c1')
    expect(query?.get('limit')).toBe('20')
    expect(query?.has('actor')).toBe(false)
    expect(log.events.value).toHaveLength(1)
    expect(log.nextCursor.value).toBe('next')
  })

  it('keeps a failure in its state rather than throwing', async () => {
    stubApi([{ path: '/audit', status: 422, body: { error: 'invalid_cursor', detail: 'bad' } }])
    const log = useAuditLog()

    await log.load({ cursor: 'garbage' })

    expect(log.status.value).toBe('error')
    expect(log.error.value).not.toBe('')
  })

  it('keeps the newest question: a slow first answer never replaces the second', async () => {
    let release: () => void = () => {}
    const slow = new Promise<void>((resolve) => {
      release = resolve
    })
    let first = true
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = new URL(input instanceof Request ? input.url : String(input))
        if (first) {
          first = false
          await slow
        }
        const event = anAuditEvent({ request_id: url.searchParams.get('actor') ?? '' })
        return new Response(JSON.stringify({ items: [event], next_cursor: null }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }),
    )
    const log = useAuditLog()

    const old = log.load({ actor: 'old' })
    await log.load({ actor: 'new' })
    release()
    await old

    expect(log.events.value[0]?.request_id).toBe('new')
  })
})
