import { render, screen } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import { createAppRouter } from '../src/router'
import LoginFailed from '../src/router/LoginFailed.vue'
import { HOME, SECTIONS } from '../src/router/sections'

function router() {
  return createAppRouter(createMemoryHistory())
}

describe('the application router', () => {
  it('lands on the home section rather than on an empty page', async () => {
    const app = router()

    await app.push('/')

    expect(app.currentRoute.value.path).toBe(HOME)
  })

  it('routes every section that has a screen behind it', () => {
    const paths = router()
      .getRoutes()
      .map((route) => route.path)

    for (const section of SECTIONS) {
      expect(paths.includes(section.path)).toBe(Boolean(section.view))
    }
  })

  it('routes the diagram builder under /diagrammes', async () => {
    const app = router()

    await app.push('/diagrammes')

    expect(app.currentRoute.value.name).toBe('diagrams')
  })

  it('falls back to a not-found screen instead of a blank one', async () => {
    const app = router()

    await app.push('/une-section-qui-nexiste-pas')

    expect(app.currentRoute.value.name).toBe('not-found')
  })
})

describe('the login gate', () => {
  it('sends a visitor without a token to Keycloak, remembering where they were going', async () => {
    const signIn = vi.fn(() => Promise.resolve())
    const app = createAppRouter(createMemoryHistory(), { accessToken: () => Promise.resolve(null), signIn })

    await app.push('/elements?element=7')

    expect(signIn).toHaveBeenCalledWith('/elements?element=7')
  })

  it('lands on a page saying the login could not start, not on a blank one, when the redirect fails', async () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})
    const signIn = vi.fn(() =>
      Promise.reject(new Error('Crypto.subtle is available only in secure contexts (HTTPS)')),
    )
    const app = createAppRouter(createMemoryHistory(), { accessToken: () => Promise.resolve(null), signIn })

    await app.push('/elements?element=7')

    expect(app.currentRoute.value.name).toBe('login-failed')
    expect(app.currentRoute.value.query.returnTo).toBe('/elements?element=7')
    expect(error).toHaveBeenCalled()
    error.mockRestore()
  })

  it('offers to try the login again, towards where the visitor was going', async () => {
    const app = createAppRouter(createMemoryHistory(), {
      accessToken: () => Promise.resolve(null),
      signIn: vi.fn(() => Promise.resolve()),
    })
    await app.push({ name: 'login-failed', query: { returnTo: '/elements?element=7' } })

    render(LoginFailed, { global: { plugins: [app] } })

    expect(screen.getByRole('alert')).toHaveTextContent(/n’a pas pu démarrer/)
    expect(screen.getByRole('link', { name: 'Réessayer' })).toHaveAttribute('href', '/elements?element=7')
  })

  it('lets the login callback through without a token', async () => {
    const signIn = vi.fn(() => Promise.resolve())
    const app = createAppRouter(createMemoryHistory(), { accessToken: () => Promise.resolve(null), signIn })

    await app.push('/auth/callback?code=x&state=y')

    expect(app.currentRoute.value.name).toBe('auth-callback')
    expect(signIn).not.toHaveBeenCalled()
  })
})
