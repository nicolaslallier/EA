import { afterEach, describe, expect, it, vi } from 'vitest'

vi.unmock('../src/lib/me')
const { resetMe, useMe } = await import('../src/lib/me')

function answering(body: unknown, status = 200) {
  return vi.fn(() =>
    Promise.resolve(
      new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
    ),
  )
}

describe('who is logged in', () => {
  afterEach(() => {
    resetMe()
    vi.unstubAllGlobals()
  })

  it('asks the API once, however many screens ask', async () => {
    const fetch = answering({ username: 'alice', can_write: true })
    vi.stubGlobal('fetch', fetch)

    await Promise.all([useMe().load(), useMe().load()])

    expect(fetch).toHaveBeenCalledTimes(1)
    expect(useMe().me.value?.username).toBe('alice')
    expect(useMe().canWrite.value).toBe(true)
  })

  it('offers no write before the API has said so', () => {
    expect(useMe().canWrite.value).toBe(false)
  })

  it('keeps a failure in its state rather than throwing', async () => {
    vi.stubGlobal('fetch', answering({ error: 'internal_error', detail: 'x' }, 500))

    await useMe().load()

    expect(useMe().error.value).not.toBeNull()
    expect(useMe().canWrite.value).toBe(false)
  })
})
