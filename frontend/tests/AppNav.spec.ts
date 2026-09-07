import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'
import type { Router } from 'vue-router'
import { createMemoryHistory } from 'vue-router'

import AppNav from '../src/components/AppNav.vue'
import { createAppRouter } from '../src/router'
import { GROUPS, SECTIONS } from '../src/router/sections'

async function renderNav(path = '/elements'): Promise<Router> {
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  await router.isReady()
  render(AppNav, { global: { plugins: [router] } })
  return router
}

const built = SECTIONS.filter((section) => section.view)
const upcoming = SECTIONS.filter((section) => !section.view)

describe('AppNav', () => {
  it('lists every group and every section it declares', async () => {
    await renderNav()

    for (const group of GROUPS) {
      expect(screen.getByRole('heading', { name: group.label })).toBeInTheDocument()
    }
    for (const section of SECTIONS) {
      expect(screen.getByText(section.label)).toBeInTheDocument()
    }
  })

  it('links the sections that have a screen behind them', async () => {
    await renderNav()

    for (const section of built) {
      expect(screen.getByRole('link', { name: new RegExp(section.label) })).toHaveAttribute(
        'href',
        section.path,
      )
    }
  })

  it('announces the sections still to come without linking them', async () => {
    expect(upcoming.length).toBeGreaterThan(0)
    await renderNav()

    for (const section of upcoming) {
      expect(screen.queryByRole('link', { name: new RegExp(section.label) })).toBeNull()
      const item = screen.getByText(section.label).closest('[aria-disabled="true"]')
      expect(item).not.toBeNull()
      expect(item).toHaveTextContent(/à venir/i)
    }
  })

  it('marks the open section as the current page', async () => {
    await renderNav('/elements')

    expect(screen.getByRole('link', { name: /Éléments/ })).toHaveAttribute('aria-current', 'page')
  })

  it('hides the sections behind a toggle, for narrow screens', async () => {
    await renderNav()
    const toggle = screen.getByRole('button', { name: /menu/i })

    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
  })

  it('closes the toggled menu once a section has been opened', async () => {
    const router = await renderNav()
    const toggle = screen.getByRole('button', { name: /menu/i })
    await fireEvent.click(toggle)

    await router.push('/une-section-qui-nexiste-pas')

    await waitFor(() => expect(toggle).toHaveAttribute('aria-expanded', 'false'))
  })
})
