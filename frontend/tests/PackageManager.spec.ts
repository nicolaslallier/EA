import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'

import PackageManager from '../src/features/packages/PackageManager.vue'
import { usePackages } from '../src/features/packages/usePackages'
import { useMe } from '../src/lib/me'
import { aPackage, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const FINANCE = aPackage({ id: 'f', name: 'Finance', path: 'Finance' })
const BUDGET = aPackage({ id: 'b', name: 'Budget', parent_id: 'f', path: 'Finance / Budget' })
const LIST: Route = { path: '/packages', body: [FINANCE, BUDGET] }

async function show(selected: string, routes: Route[] = []) {
  const calls = stubApi([...routes, LIST])
  const store = usePackages()
  await store.load()
  const view = render(PackageManager, { props: { store, selected } })
  return { calls, view }
}

describe('PackageManager', () => {
  it('offers a reader nothing at all', async () => {
    vi.mocked(useMe).mockReturnValueOnce({
      me: ref({ username: 'reader', can_write: false, can_approve: false }),
      canWrite: computed(() => false),
      error: ref(null),
      load: vi.fn(() => Promise.resolve()),
    })

    await show('f')

    expect(screen.queryByRole('toolbar')).toBeNull()
    expect(screen.queryByRole('button', { name: /nouveau paquet/i })).toBeNull()
  })

  it('disables rename, move and delete while nothing is selected — or the package is gone', async () => {
    await show('disparu')

    expect(screen.getByRole('button', { name: /nouveau paquet/i })).toBeEnabled()
    for (const name of [/renommer/i, /déplacer/i, /supprimer/i]) {
      expect(screen.getByRole('button', { name })).toBeDisabled()
    }
  })

  it('creates a package at the root when nothing is selected, and selects it', async () => {
    const { calls, view } = await show('', [
      { method: 'POST', path: '/packages', status: 201, body: aPackage({ id: 'n', name: 'RH' }) },
    ])

    await fireEvent.click(screen.getByRole('button', { name: /nouveau paquet/i }))
    await fireEvent.update(screen.getByLabelText(/nom du paquet/i), 'RH')
    await fireEvent.submit(screen.getByRole('form', { name: /nouveau paquet/i }))

    await waitFor(() =>
      expect(calls.find((call) => call.method === 'POST')?.body).toEqual({
        name: 'RH',
        parent_id: null,
      }),
    )
    await waitFor(() => expect(view.emitted().select).toEqual([['n']]))
  })

  it('creates inside the selected package', async () => {
    const { calls } = await show('f', [
      { method: 'POST', path: '/packages', status: 201, body: aPackage({ id: 'n' }) },
    ])

    await fireEvent.click(screen.getByRole('button', { name: /nouveau paquet/i }))
    await fireEvent.update(screen.getByLabelText(/nom du paquet/i), 'Achats')
    await fireEvent.submit(screen.getByRole('form', { name: /nouveau paquet/i }))

    await waitFor(() =>
      expect(calls.find((call) => call.method === 'POST')?.body).toEqual({
        name: 'Achats',
        parent_id: 'f',
      }),
    )
  })

  it('renames, prefilled with the current name', async () => {
    const { calls } = await show('b', [
      { method: 'PATCH', path: '/packages/b', body: aPackage({ id: 'b' }) },
    ])

    await fireEvent.click(screen.getByRole('button', { name: /renommer/i }))
    const field = screen.getByLabelText(/nom du paquet/i)
    expect(field).toHaveValue('Budget')
    await fireEvent.update(field, 'Prévisions')
    await fireEvent.submit(screen.getByRole('form', { name: /renommer/i }))

    await waitFor(() =>
      expect(calls.find((call) => call.method === 'PATCH')?.body).toEqual({ name: 'Prévisions' }),
    )
  })

  it('moves to the root with an explicit null, and never offers the package itself as parent', async () => {
    const { calls } = await show('b', [
      { method: 'PATCH', path: '/packages/b', body: aPackage({ id: 'b' }) },
    ])

    await fireEvent.click(screen.getByRole('button', { name: /déplacer/i }))
    const parent = screen.getByLabelText(/nouveau parent/i)
    expect(Array.from(parent.querySelectorAll('option')).map((o) => o.textContent?.trim())).toEqual([
      'Racine',
      'Finance',
    ])
    await fireEvent.update(parent, 'root')
    await fireEvent.submit(screen.getByRole('form', { name: /déplacer/i }))

    await waitFor(() =>
      expect(calls.find((call) => call.method === 'PATCH')?.body).toEqual({ parent_id: null }),
    )
  })

  it('shows why a non-empty package was refused and deletes once told where its contents go', async () => {
    const { calls, view } = await show('f', [
      {
        method: 'DELETE',
        path: '/packages/f',
        status: 409,
        body: { error: 'package_not_empty', detail: 'Finance contient 2 éléments.' },
      },
    ])

    await fireEvent.click(screen.getByRole('button', { name: /supprimer/i }))
    await fireEvent.submit(screen.getByRole('form', { name: /supprimer/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Finance contient 2 éléments.')

    const second = stubApi([{ method: 'DELETE', path: '/packages/f', status: 204 }, LIST])
    await fireEvent.update(screen.getByLabelText(/contenu/i), 'root')
    await fireEvent.submit(screen.getByRole('form', { name: /supprimer/i }))

    await waitFor(() => expect(view.emitted().select).toEqual([['']]))
    const refused = calls.find((call) => call.method === 'DELETE')
    const accepted = second.find((call) => call.method === 'DELETE')
    expect(refused?.url.searchParams.has('move_contents_to')).toBe(false)
    expect(accepted?.url.searchParams.get('move_contents_to')).toBe('root')
  })

  it('closes the open form when another package is selected, so it never acts on the new one', async () => {
    const calls = stubApi([
      { method: 'PATCH', path: '/packages/b', body: aPackage({ id: 'b' }) },
      LIST,
    ])
    const store = usePackages()
    await store.load()
    const view = render(PackageManager, { props: { store, selected: 'f' } })

    await fireEvent.click(screen.getByRole('button', { name: /renommer/i }))
    await fireEvent.update(screen.getByLabelText(/nom du paquet/i), 'Autre nom')
    await view.rerender({ selected: 'b' })

    expect(screen.queryByRole('form')).toBeNull()
    expect(calls.some((call) => call.method === 'PATCH')).toBe(false)
  })
})
