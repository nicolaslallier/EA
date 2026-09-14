import { render, screen } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import App from '../src/App.vue'
import { createAppRouter } from '../src/router'

async function renderApp(path: string) {
  // The shell mounts BackendStatus, which probes /health on mount; a routed
  // screen may list something on mount too, and gets an empty list.
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input)
      const body = url.endsWith('/health') ? { status: 'ok' } : []
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }))
    }),
  )
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  await router.isReady()
  return render(App, { global: { plugins: [router] } })
}

describe('the application shell', () => {
  it('frames the routed screen with the section menu', async () => {
    await renderApp('/une-section-qui-nexiste-pas')

    expect(screen.getByRole('navigation', { name: /sections/i })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /introuvable/i })).toBeInTheDocument()
  })
})
