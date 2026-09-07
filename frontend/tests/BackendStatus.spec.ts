import { render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import BackendStatus from '../src/components/BackendStatus.vue'

afterEach(() => {
  vi.unstubAllGlobals()
})

function stubFetch(impl: typeof fetch) {
  vi.stubGlobal('fetch', vi.fn(impl))
}

describe('BackendStatus', () => {
  it('shows the backend status once the call resolves', async () => {
    stubFetch(async () => new Response(JSON.stringify({ status: 'ok' }), { status: 200 }))

    render(BackendStatus)

    expect(screen.getByText(/vérification/i)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText(/backend: ok/i)).toBeInTheDocument())
  })

  it('reports an unreachable backend instead of staying blank', async () => {
    stubFetch(async () => {
      throw new TypeError('Failed to fetch')
    })

    render(BackendStatus)

    await waitFor(() => expect(screen.getByText(/injoignable/i)).toBeInTheDocument())
  })

  it('reports a non-2xx response as an error', async () => {
    stubFetch(async () => new Response('nope', { status: 503 }))

    render(BackendStatus)

    await waitFor(() => expect(screen.getByText(/injoignable/i)).toBeInTheDocument())
  })
})
