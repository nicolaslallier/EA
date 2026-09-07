import { describe, expect, it } from 'vitest'
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
