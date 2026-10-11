import { fireEvent, render, screen, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import HistorySection from '../src/features/history/HistorySection.vue'
import { createAppRouter } from '../src/router'
import { anAuditEvent, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

/** The list of events — not the filters, whose options carry the same labels. */
async function trail() {
  return within(await screen.findByRole('list'))
}

async function open(query = '', body: unknown = { items: [anAuditEvent()], next_cursor: null }) {
  const calls = stubApi([{ path: '/audit', body }])
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/history${query}`)
  await router.isReady()
  const { container } = render(HistorySection, { global: { plugins: [router] } })
  return { calls, router, container }
}

describe('HistorySection', () => {
  it('lists the trail and unfolds a line into its field-by-field diff', async () => {
    await open()

    expect((await trail()).getByText('Élément modifié')).toBeTruthy()
    expect(screen.getByText('editor')).toBeTruthy()
    expect(screen.queryByText('db-02')).toBeNull() // folded until asked
    await fireEvent.click(screen.getByText('Voir les champs'))
    expect(screen.getByText('db-02')).toBeTruthy()
  })

  it('reads its filters from the URL', async () => {
    const { calls } = await open('?kind=element&entity=abc&actor=editor&action=element.updated')

    await trail()
    const query = calls[0]?.url.searchParams
    expect(query?.get('entity_kind')).toBe('element')
    expect(query?.get('entity_id')).toBe('abc')
    expect(query?.get('actor')).toBe('editor')
    expect(query?.get('action')).toBe('element.updated')
  })

  it('walks to the next page by writing the cursor in the URL', async () => {
    const { router } = await open('', { items: [anAuditEvent()], next_cursor: 'page-2' })

    await fireEvent.click(await screen.findByRole('button', { name: 'Page suivante' }))

    await vi.waitFor(() => expect(router.currentRoute.value.query.cursor).toBe('page-2'))
  })

  it('links an element line to the element', async () => {
    await open()

    const link = await screen.findByRole('link', { name: "Voir l'élément" })
    expect(link.getAttribute('href')).toContain('element=22222222-2222-4222-8222-222222222222')
  })

  it('says that nothing matches, without a loading notice left behind', async () => {
    await open('', { items: [], next_cursor: null })

    expect(await screen.findByText('Aucun événement.')).toBeTruthy()
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('shows a refusal in words, apart from the empty case', async () => {
    const calls = stubApi([
      { path: '/audit', status: 422, body: { error: 'invalid_cursor', detail: 'cursor is bad' } },
    ])
    const router = createAppRouter(createMemoryHistory())
    await router.push('/history?cursor=forged')
    await router.isReady()
    render(HistorySection, { global: { plugins: [router] } })

    expect((await screen.findByRole('alert')).textContent).toContain('cursor is bad')
    expect(screen.queryByText('Aucun événement.')).toBeNull()
    expect(calls).toHaveLength(1)
  })

  it('ignores a kind or an action the labels do not know', async () => {
    const { calls } = await open('?kind=galaxy&action=element.exploded')

    await trail()
    expect(calls[0]?.url.searchParams.has('entity_kind')).toBe(false)
    expect(calls[0]?.url.searchParams.has('action')).toBe(false)
  })

  it('knows the kinds the backend added, packages and saved queries', async () => {
    await open()

    const kinds = screen.getAllByRole('option').map((option) => option.textContent)
    expect(kinds).toContain('Paquet')
    expect(kinds).toContain('Requête enregistrée')
    expect(kinds).toContain('Paquet créé')
  })

  it('writes the filters in the URL, replacing the entry, and drops the old cursor', async () => {
    const { router } = await open('?cursor=old')
    await trail()

    await fireEvent.update(screen.getByLabelText('Acteur'), 'alice')
    await fireEvent.click(screen.getByRole('button', { name: 'Filtrer' }))

    await vi.waitFor(() => expect(router.currentRoute.value.query).toEqual({ actor: 'alice' }))
  })

  it('shows what an event carries as text, not as markup', async () => {
    const { container } = await open('', {
      items: [anAuditEvent({ actor_name: '<b>mallory</b>' })],
      next_cursor: null,
    })

    await screen.findByText('<b>mallory</b>')
    expect(container.querySelector('b')).toBeNull()
  })

  it('offers no control that writes', async () => {
    await open()
    await trail()

    const names = screen.getAllByRole('button').map((button) => button.textContent?.trim())
    expect(names).toEqual(['Filtrer', 'Voir les champs'])
  })

  it('comes back to the first page', async () => {
    const { router } = await open('?cursor=p2')

    await fireEvent.click(await screen.findByRole('button', { name: 'Première page' }))

    await vi.waitFor(() => expect(router.currentRoute.value.query.cursor).toBeUndefined())
  })
})
