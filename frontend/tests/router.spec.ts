import { describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import { createAppRouter } from '../src/router'
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

  it('lets the login callback through without a token', async () => {
    const signIn = vi.fn(() => Promise.resolve())
    const app = createAppRouter(createMemoryHistory(), { accessToken: () => Promise.resolve(null), signIn })

    await app.push('/auth/callback?code=x&state=y')

    expect(app.currentRoute.value.name).toBe('auth-callback')
    expect(signIn).not.toHaveBeenCalled()
  })
})
