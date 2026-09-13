import { render, screen } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import App from '../src/App.vue'
import { createAppRouter } from '../src/router'

async function renderApp(path: string) {
  // The shell mounts BackendStatus, which probes /health on mount.
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve(new Response(JSON.stringify({ status: 'ok' }), { status: 200 }))),
  )
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  await router.isReady()
  render(App, { global: { plugins: [router] } })
}

describe('the application shell', () => {
  it('frames the routed screen with the section menu', async () => {
    await renderApp('/une-section-qui-nexiste-pas')

    expect(screen.getByRole('navigation', { name: /sections/i })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /introuvable/i })).toBeInTheDocument()
  })
})
