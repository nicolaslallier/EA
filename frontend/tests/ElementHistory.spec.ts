import { fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import ElementHistory from '../src/features/history/ElementHistory.vue'
import { createAppRouter } from '../src/router'
import { anAuditEvent, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

function mount() {
  const calls = stubApi([{ path: '/audit', body: { items: [anAuditEvent()], next_cursor: null } }])
  const router = createAppRouter(createMemoryHistory())
  const { container } = render(ElementHistory, {
    props: { elementId: '22222222-2222-4222-8222-222222222222' },
    global: { plugins: [router] },
  })
  return { calls, container }
}

/** Open the panel the way the browser does: the `open` attribute, then a `toggle` event. */
async function open(container: Element): Promise<void> {
  const details = container.querySelector('details')
  if (!details) {
    throw new Error('no <details> rendered')
  }
  details.open = true
  await fireEvent(details, new Event('toggle'))
}

describe('ElementHistory', () => {
  it('asks nothing until the panel is opened', () => {
    const { calls } = mount()

    expect(calls).toHaveLength(0)
  })

  it('then lists the trail of that element and links to the whole of it', async () => {
    const { calls, container } = mount()

    await open(container)

    expect(await screen.findByText('Élément modifié')).toBeTruthy()
    expect(calls[0]?.url.searchParams.get('entity_kind')).toBe('element')
    expect(calls[0]?.url.searchParams.get('entity_id')).toBe('22222222-2222-4222-8222-222222222222')
    expect(screen.getByRole('link', { name: 'Tout l’historique' }).getAttribute('href')).toContain(
      'entity=22222222-2222-4222-8222-222222222222',
    )
  })

  it('says when an element has no history yet', async () => {
    stubApi([{ path: '/audit', body: { items: [], next_cursor: null } }])
    const router = createAppRouter(createMemoryHistory())
    const { container } = render(ElementHistory, {
      props: { elementId: '22222222-2222-4222-8222-222222222222' },
      global: { plugins: [router] },
    })

    await open(container)

    expect(await screen.findByText('Aucun événement.')).toBeTruthy()
  })

  it('shows a failure in words', async () => {
    stubApi([{ path: '/audit', status: 500, body: { error: 'internal_error', detail: 'boom' } }])
    const router = createAppRouter(createMemoryHistory())
    const { container } = render(ElementHistory, {
      props: { elementId: '22222222-2222-4222-8222-222222222222' },
      global: { plugins: [router] },
    })

    await open(container)

    expect((await screen.findByRole('alert')).textContent).toContain('boom')
  })
})
