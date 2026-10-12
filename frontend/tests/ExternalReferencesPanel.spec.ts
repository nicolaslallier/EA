import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'

import ExternalReferencesPanel from '../src/features/external/ExternalReferencesPanel.vue'
import { ago, safeHref } from '../src/features/external/useExternalReferences'
import { useMe } from '../src/lib/me'
import { anElement, stubApi } from './support/api'
import type { Route } from './support/api'

const ELEMENT = anElement({ name: 'Facturation' })
const LIST = `/elements/${ELEMENT.id}/external-references`
const REF = 'rrrrrrrr-1111-4111-8111-111111111111'

function aReference(overrides: Record<string, unknown> = {}) {
  return {
    id: REF,
    element_id: ELEMENT.id,
    kind: 'github_issue',
    target: 'main',
    external_id: 'acme/billing#12',
    url: 'https://github.test/acme/billing/issues/12',
    title: 'Fix invoice rounding',
    status: 'open',
    synced_at: new Date().toISOString(),
    created_by: 'editor',
    created_at: '2026-10-11T10:00:00Z',
    stale: false,
    ...overrides,
  }
}

const TARGETS = {
  path: '/external/targets',
  body: [
    { name: 'main', kind: 'github_issue', active: true, writable: true },
    { name: 'wiki', kind: 'confluence_page', active: false, writable: false },
  ],
}

function show(routes: Route[]) {
  const calls = stubApi([TARGETS, ...routes])
  const rendered = render(ExternalReferencesPanel, { props: { element: ELEMENT } })
  return { calls, rendered }
}

afterEach(() => vi.unstubAllGlobals())

describe('ExternalReferencesPanel', () => {
  it('lists what the element cites with the cached title, as a link that opens elsewhere safely', async () => {
    show([{ path: LIST, body: [aReference()] }])

    const link = await screen.findByRole('link', { name: 'Fix invoice rounding' })
    expect(link).toHaveAttribute('href', 'https://github.test/acme/billing/issues/12')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.getByText('ouvert')).toBeInTheDocument()
    expect(screen.getByText('Ticket GitHub')).toBeInTheDocument()
  })

  it('renders a third-party title as text, never as markup', async () => {
    show([{ path: LIST, body: [aReference({ title: '<img src=x onerror=alert(1)><b>gras</b>' })] }])

    const panel = within(await screen.findByRole('region', { name: /références de l'élément/i }))
    expect(await panel.findByText('<img src=x onerror=alert(1)><b>gras</b>')).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
    expect(document.querySelector('b')).toBeNull()
  })

  it('never makes a link of an address that is not http(s)', async () => {
    show([
      {
        path: LIST,
        body: [
          aReference({
            kind: 'url',
            target: '',
            title: '',
            external_id: 'javascript:alert(1)',
            url: 'javascript:alert(1)',
          }),
        ],
      },
    ])

    expect(await screen.findByText('javascript:alert(1)')).toBeInTheDocument()
    expect(screen.queryByRole('link')).toBeNull()
    expect(safeHref('javascript:alert(1)')).toBeUndefined()
    expect(safeHref('https://x.test/a')).toBe('https://x.test/a')
    expect(safeHref('not a url')).toBeUndefined()
  })

  it('warns that the cache is old and the provider may be down, and keeps the screen usable', async () => {
    show([{ path: LIST, body: [aReference({ stale: true })] }])

    expect(await screen.findByText(/titre et état affichés viennent du cache/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /actualiser fix invoice rounding/i })).toBeEnabled()
  })

  it('says the element cites nothing rather than showing an empty table', async () => {
    show([{ path: LIST, body: [] }])

    expect(await screen.findByText(/aucune référence/i)).toBeInTheDocument()
  })

  it('says why when the references cannot be read', async () => {
    show([{ path: LIST, status: 404, body: { error: 'not_found', detail: "Cet élément n'existe pas" } }])

    expect(await screen.findByRole('alert')).toHaveTextContent(/n'existe pas/)
  })

  it('links an object by naming a target, never giving an address to call', async () => {
    const { calls } = show([
      { path: LIST, body: [] },
      { method: 'POST', path: LIST, status: 201, body: aReference() },
    ])
    await screen.findByText(/aucune référence/i)

    await fireEvent.update(screen.getByLabelText(/référence/i, { selector: 'input' }), 'acme/billing#12')
    await fireEvent.click(screen.getByRole('button', { name: /^ajouter$/i }))

    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true))
    const post = calls.find((c) => c.method === 'POST')
    expect(post?.body).toEqual({ kind: 'github_issue', reference: 'acme/billing#12', target: 'main' })
  })

  it('offers only the active targets, and the free link', async () => {
    show([{ path: LIST, body: [] }])
    await screen.findByText(/aucune référence/i)

    const options = within(screen.getByLabelText(/^cible$/i)).getAllByRole('option')
    expect(options.map((o) => o.textContent)).toEqual(['main — Ticket GitHub', 'Lien libre (url)'])
  })

  it('shows the server refusal in words', async () => {
    show([
      { path: LIST, body: [] },
      {
        method: 'POST',
        path: LIST,
        status: 422,
        body: { error: 'external_refused', detail: 'acme/billing#999 was not found on GitHub' },
      },
    ])
    await screen.findByText(/aucune référence/i)

    await fireEvent.update(screen.getByLabelText(/référence/i, { selector: 'input' }), 'acme/billing#999')
    await fireEvent.click(screen.getByRole('button', { name: /^ajouter$/i }))

    expect(await screen.findByText(/was not found on GitHub/)).toBeInTheDocument()
  })

  it('refreshes one line', async () => {
    const { calls } = show([
      { path: LIST, body: [aReference()] },
      { method: 'POST', path: `/external-references/${REF}/refresh`, body: aReference() },
    ])

    await fireEvent.click(
      await screen.findByRole('button', { name: /actualiser fix invoice rounding/i }),
    )

    await waitFor(() =>
      expect(calls.some((c) => c.url.pathname === `/external-references/${REF}/refresh`)).toBe(true),
    )
  })

  it('asks before removing a link', async () => {
    const { calls } = show([
      { path: LIST, body: [aReference()] },
      { method: 'DELETE', path: `/external-references/${REF}`, status: 204 },
    ])

    await fireEvent.click(await screen.findByRole('button', { name: /retirer fix invoice rounding/i }))
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
    await fireEvent.click(screen.getByRole('button', { name: /confirmer/i }))

    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true))
  })

  it('offers a reader the list and nothing that writes', async () => {
    vi.mocked(useMe).mockReturnValueOnce({
      me: ref({ username: 'reader', can_write: false, can_approve: false }),
      canWrite: computed(() => false),
      error: ref(null),
      load: vi.fn(() => Promise.resolve()),
    })
    show([{ path: LIST, body: [aReference()] }])

    expect(await screen.findByRole('link', { name: 'Fix invoice rounding' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^ajouter$/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /retirer/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /actualiser/i })).toBeNull()
    expect(screen.queryByText(/ouvrir un ticket/i)).toBeNull()
  })

  it('previews a ticket before sending it, and sends nothing on the preview', async () => {
    const { calls } = show([
      { path: LIST, body: [] },
      {
        method: 'POST',
        path: `/elements/${ELEMENT.id}/external-issues`,
        body: {
          dry_run: true,
          target: 'main',
          scope: 'acme/billing',
          title: 'Rounding',
          body: 'why',
          reference: null,
        },
      },
    ])
    await screen.findByText(/aucune référence/i)

    await fireEvent.click(screen.getByText(/ouvrir un ticket/i))
    await fireEvent.update(screen.getByLabelText(/dépôt/i), 'acme/billing')
    await fireEvent.update(screen.getByLabelText(/titre du ticket/i), 'Rounding')
    await fireEvent.click(screen.getByRole('button', { name: /prévisualiser/i }))

    expect(await screen.findByText(/rien n'a été envoyé/i)).toBeInTheDocument()
    const post = calls.find((c) => c.method === 'POST')
    expect(post?.body).toMatchObject({ dry_run: true, target: 'main', scope: 'acme/billing' })
    expect(screen.getByRole('button', { name: /^ouvrir le ticket$/i })).toBeInTheDocument()
  })

  it('closes on demand', async () => {
    const { rendered } = show([{ path: LIST, body: [] }])
    await screen.findByText(/aucune référence/i)

    await fireEvent.click(screen.getAllByRole('button', { name: /^fermer$/i })[0])

    expect(rendered.emitted().close).toBeTruthy()
  })
})

describe('ago', () => {
  const now = new Date('2026-10-11T12:00:00Z')

  it('says how long ago in French and nothing for a cache never filled', () => {
    expect(ago('2026-10-11T09:00:00Z', now)).toMatch(/3 heures/)
    expect(ago('2026-10-09T12:00:00Z', now)).toMatch(/avant-hier|2 jours/)
    expect(ago(null, now)).toBe('')
  })
})
