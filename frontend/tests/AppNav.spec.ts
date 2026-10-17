import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'
import type { Router } from 'vue-router'
import { createMemoryHistory } from 'vue-router'

import AppNav from '../src/components/AppNav.vue'
import { createAppRouter } from '../src/router'
import { GROUPS, menu, SECTIONS, type MenuEntry } from '../src/router/sections'

async function renderNav(path = '/elements', entries?: MenuEntry[]): Promise<Router> {
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  await router.isReady()
  render(AppNav, { props: entries ? { entries } : {}, global: { plugins: [router] } })
  return router
}

const built = SECTIONS.filter((section) => section.view)

/**
 * A menu with a section that has no screen.
 *
 * Every declared section has one today, so the catalogue itself can no longer
 * produce this case — and the badge is still what the menu must show the day
 * the next section is declared before it is built.
 */
const WITH_AN_UPCOMING_SECTION: MenuEntry[] = [
  {
    group: GROUPS[0],
    sections: [
      {
        path: '/analyse/scenarios',
        name: 'scenarios',
        label: 'Scénarios',
        summary: 'Déclarée, pas encore construite.',
        group: GROUPS[0].id,
      },
    ],
  },
]

/** Opens the dropdown of one group, as a click on its button does. */
async function openGroup(label: string): Promise<HTMLElement> {
  const button = screen.getByRole('button', { name: new RegExp(`^${label}`) })
  await fireEvent.click(button)
  return button
}

describe('AppNav', () => {
  it('offers one button per group, every dropdown closed', async () => {
    await renderNav()

    for (const entry of menu()) {
      const button = screen.getByRole('button', { name: new RegExp(`^${entry.group.label}`) })
      expect(button).toHaveAttribute('aria-expanded', 'false')
      expect(button).toHaveAttribute('aria-controls')
    }
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('opens a group on click, its sections linked with their summary', async () => {
    await renderNav()

    for (const entry of menu()) {
      const button = await openGroup(entry.group.label)
      expect(button).toHaveAttribute('aria-expanded', 'true')
      const panel = document.getElementById(button.getAttribute('aria-controls') ?? '')
      expect(panel).not.toBeNull()
      for (const section of entry.sections.filter((s) => s.view)) {
        const link = screen.getByRole('link', { name: new RegExp(section.label) })
        expect(link).toHaveAttribute('href', section.path)
        expect(link).toHaveTextContent(section.summary)
      }
    }
  })

  it('keeps only one dropdown open at a time', async () => {
    await renderNav()
    const [first, second] = menu()

    const a = await openGroup(first.group.label)
    const b = await openGroup(second.group.label)

    expect(a).toHaveAttribute('aria-expanded', 'false')
    expect(b).toHaveAttribute('aria-expanded', 'true')
  })

  it('closes on a second click of the same group', async () => {
    await renderNav()
    const [first] = menu()

    await openGroup(first.group.label)
    const button = await openGroup(first.group.label)

    expect(button).toHaveAttribute('aria-expanded', 'false')
  })

  it('closes on Escape and gives the focus back to its group', async () => {
    await renderNav()
    const [first] = menu()
    const button = await openGroup(first.group.label)
    const link = screen.getAllByRole('link')[0]
    link.focus()

    await fireEvent.keyDown(link, { key: 'Escape' })

    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(button).toHaveFocus()
  })

  it('closes on a click outside the menu', async () => {
    await renderNav()
    const [first] = menu()
    const button = await openGroup(first.group.label)

    await fireEvent.pointerDown(document.body)

    expect(button).toHaveAttribute('aria-expanded', 'false')
  })

  it('closes once a section has been opened', async () => {
    const router = await renderNav()
    const [first] = menu()
    const button = await openGroup(first.group.label)

    await router.push('/une-section-qui-nexiste-pas')

    await waitFor(() => expect(button).toHaveAttribute('aria-expanded', 'false'))
  })

  it('closes when the focus leaves the menu', async () => {
    await renderNav()
    const [first] = menu()
    const button = await openGroup(first.group.label)
    const outside = document.createElement('button')
    document.body.appendChild(outside)

    await fireEvent.focusOut(screen.getAllByRole('link')[0], { relatedTarget: outside })

    expect(button).toHaveAttribute('aria-expanded', 'false')
    outside.remove()
  })

  it('stays open while the focus moves inside the menu', async () => {
    await renderNav()
    const [first] = menu()
    const button = await openGroup(first.group.label)
    const [a, b] = screen.getAllByRole('link')

    await fireEvent.focusOut(a, { relatedTarget: b })

    expect(button).toHaveAttribute('aria-expanded', 'true')
  })

  it('closes on a click of the link to the page already open', async () => {
    await renderNav('/elements')
    const toggle = screen.getByRole('button', { name: /^☰ menu/i })
    await fireEvent.click(toggle)
    const button = await openGroup('Modèle')

    await fireEvent.click(screen.getByRole('link', { name: /Éléments/ }))

    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
  })

  it('folds the narrow-screen menu too, on Escape or a click outside', async () => {
    await renderNav()
    const toggle = screen.getByRole('button', { name: /^☰ menu/i })
    const [first] = menu()

    await fireEvent.click(toggle)
    await openGroup(first.group.label)
    await fireEvent.keyDown(screen.getAllByRole('link')[0], { key: 'Escape' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')

    await fireEvent.click(toggle)
    await fireEvent.pointerDown(document.body)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
  })

  it('marks the group of the open section, nested paths included', async () => {
    await renderNav('/analyse/impact')
    const impact = SECTIONS.find((section) => section.path === '/analyse/impact')
    const group = GROUPS.find((g) => g.id === impact?.group)

    for (const entry of menu()) {
      const button = screen.getByRole('button', { name: new RegExp(`^${entry.group.label}`) })
      if (entry.group.id === group?.id) {
        expect(button).toHaveAttribute('aria-current', 'true')
      } else {
        expect(button).not.toHaveAttribute('aria-current')
      }
    }
  })

  it('marks the open section as the current page', async () => {
    await renderNav('/elements')
    await openGroup('Modèle')

    expect(screen.getByRole('link', { name: /Éléments/ })).toHaveAttribute('aria-current', 'page')
  })

  it('announces a section still to come without linking it', async () => {
    await renderNav('/elements', WITH_AN_UPCOMING_SECTION)
    const [section] = WITH_AN_UPCOMING_SECTION[0].sections
    await openGroup(WITH_AN_UPCOMING_SECTION[0].group.label)

    expect(screen.queryByRole('link', { name: new RegExp(section.label) })).toBeNull()
    const item = screen.getByText(section.label).closest('[aria-disabled="true"]')
    expect(item).not.toBeNull()
    expect(item).toHaveTextContent(/à venir/i)
  })

  it('links every section the catalogue declares, none being upcoming today', async () => {
    await renderNav()

    for (const entry of menu()) {
      await openGroup(entry.group.label)
      for (const section of entry.sections) {
        expect(screen.getByText(section.label).closest('a')).not.toBeNull()
      }
    }
    expect(built.length).toBe(SECTIONS.length)
  })

  it('hides the groups behind a toggle, for narrow screens', async () => {
    await renderNav()
    const toggle = screen.getByRole('button', { name: /^☰ menu/i })

    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
  })

  it('closes the toggled menu once a section has been opened', async () => {
    const router = await renderNav()
    const toggle = screen.getByRole('button', { name: /^☰ menu/i })
    await fireEvent.click(toggle)

    await router.push('/une-section-qui-nexiste-pas')

    await waitFor(() => expect(toggle).toHaveAttribute('aria-expanded', 'false'))
  })
})

describe('the reading section', () => {
  it('is listed under its own group', async () => {
    await renderNav('/view')
    await openGroup('Lecture')

    expect(screen.getByText('Lecture')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Consultation/ })).toHaveAttribute('href', '/view')
  })
})
