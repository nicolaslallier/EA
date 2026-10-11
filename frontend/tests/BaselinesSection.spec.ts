import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'
import { createMemoryHistory } from 'vue-router'

import BaselinesSection from '../src/features/baselines/BaselinesSection.vue'
import { useMe } from '../src/lib/me'
import { createAppRouter } from '../src/router'
import { aBaseline, aDiff, aRestore, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const LIST: Route = { path: '/baselines', body: [aBaseline()] }
const DIFF_PATH = `/baselines/${aBaseline().id}/diff`

async function open(query = '', routes: Route[] = [LIST]) {
  const calls = stubApi(routes)
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/baselines${query}`)
  await router.isReady()
  const view = render(BaselinesSection, { global: { plugins: [router] } })
  return { calls, router, ...view }
}

function asAReader(): void {
  vi.mocked(useMe).mockReturnValueOnce({
    me: ref({ username: 'reader', can_write: false, can_approve: false }),
    canWrite: computed(() => false),
    error: ref(null),
    load: vi.fn(() => Promise.resolve()),
  })
}

describe('BaselinesSection', () => {
  it('links each baseline to its own history', async () => {
    await open()

    const link = await screen.findByRole('link', { name: 'Historique' })
    expect(link.getAttribute('href')).toContain('kind=baseline')
    expect(link.getAttribute('href')).toContain(`entity=${aBaseline().id}`)
  })

  it('lists the baselines with what each holds', async () => {
    await open()

    expect(await screen.findByText('Avant migration')).toBeTruthy()
    expect(screen.getByText(/12 éléments/)).toBeTruthy()
  })

  it('says it is loading, then that there is nothing, as two different things', async () => {
    await open('', [{ path: '/baselines', body: [] }])

    expect(screen.getByRole('status').textContent).toContain('Chargement')
    expect(await screen.findByText('Aucune baseline.')).toBeTruthy()
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('shows a failed load as an error and not as an empty list', async () => {
    await open('', [
      {
        path: '/baselines',
        status: 500,
        body: { error: 'internal_error', detail: 'boom' },
      },
    ])

    expect((await screen.findByRole('alert')).textContent).toContain('boom')
    expect(screen.queryByText('Aucune baseline.')).toBeNull()
  })

  it('captures a baseline under the name typed', async () => {
    const { calls } = await open('', [
      LIST,
      {
        method: 'POST',
        path: '/baselines',
        status: 201,
        body: aBaseline({ name: 'v2' }),
      },
    ])

    await fireEvent.update(await screen.findByLabelText('Nom'), 'v2')
    await fireEvent.click(screen.getByRole('button', { name: 'Capturer' }))

    await waitFor(() =>
      expect(calls.find((c) => c.method === 'POST')?.body).toEqual({
        name: 'v2',
        description: '',
      }),
    )
  })

  it('shows a refused capture and keeps what was typed', async () => {
    await open('', [
      LIST,
      {
        method: 'POST',
        path: '/baselines',
        status: 409,
        body: { error: 'duplicate', detail: 'a baseline is already named v1' },
      },
    ])

    await fireEvent.update(await screen.findByLabelText('Nom'), 'v1')
    await fireEvent.click(screen.getByRole('button', { name: 'Capturer' }))

    expect((await screen.findByRole('alert')).textContent).toContain('already named')
    expect(screen.getByLabelText<HTMLInputElement>('Nom').value).toBe('v1')
  })

  it('shows the three columns of a diff read from the URL, and escapes what it shows', async () => {
    const { calls, container } = await open(`?a=${aBaseline().id}`, [
      LIST,
      {
        path: DIFF_PATH,
        body: aDiff({
          elements_added: [{ kind: 'element', id: 'x', label: '<b>new</b>', type: 'node' }],
          elements_removed: [{ kind: 'element', id: 'y', label: 'gone', type: 'node' }],
          elements_modified: [
            {
              kind: 'element',
              id: 'z',
              label: 'billing',
              type: 'node',
              changes: [{ field: 'name', before: 'a', after: 'renamed' }],
            },
          ],
        }),
      },
    ])

    expect(await screen.findByText('<b>new</b>')).toBeTruthy()
    expect(container.querySelector('b')).toBeNull()
    expect(screen.getByText('gone')).toBeTruthy()
    expect(screen.getByText('billing')).toBeTruthy()
    expect(screen.getByRole('heading', { name: /Ajoutés/ })).toBeTruthy()
    expect(screen.getByRole('heading', { name: /Supprimés/ })).toBeTruthy()
    expect(screen.getByRole('heading', { name: /Modifiés/ })).toBeTruthy()
    expect(calls.some((c) => c.url.pathname.endsWith('/diff'))).toBe(true)
  })

  it('compares with a second baseline named in the URL', async () => {
    const other = aBaseline({
      id: '44444444-4444-4444-8444-444444444444',
      name: 'Après',
    })
    const { calls } = await open(`?a=${aBaseline().id}&b=${other.id}`, [
      { path: '/baselines', body: [aBaseline(), other] },
      { path: DIFF_PATH, body: aDiff() },
    ])

    await screen.findByText('Aucune différence.')

    expect(calls.find((c) => c.url.pathname === DIFF_PATH)?.url.searchParams.get('against')).toBe(
      other.id,
    )
  })

  it('tells an unchanged model from a diff that failed', async () => {
    await open(`?a=${aBaseline().id}`, [
      LIST,
      {
        path: DIFF_PATH,
        status: 404,
        body: { error: 'not_found', detail: 'gone' },
      },
    ])

    expect((await screen.findByRole('alert')).textContent).toContain('gone')
    expect(screen.queryByText('Aucune différence.')).toBeNull()
  })

  it('lists a long column in part and says how many more there are', async () => {
    const added = Array.from({ length: 130 }, (_, i) => ({
      kind: 'element',
      id: `id-${i}`,
      label: `added-${String(i).padStart(3, '0')}`,
      type: 'node',
    }))
    await open(`?a=${aBaseline().id}`, [
      LIST,
      { path: DIFF_PATH, body: aDiff({ elements_added: added }) },
    ])

    expect(await screen.findByText('added-000')).toBeTruthy()
    expect(screen.queryByText('added-129')).toBeNull()
    await fireEvent.click(screen.getByRole('button', { name: /30 autres/ }))
    expect(screen.getByText('added-129')).toBeTruthy()
  })

  it('writes the chosen pair in the URL', async () => {
    const { router } = await open()

    await fireEvent.click(await screen.findByRole('button', { name: 'Comparer au modèle courant' }))

    await waitFor(() => expect(router.currentRoute.value.query.a).toBe(aBaseline().id))
  })

  it('asks before deleting a baseline, and forgets it in the URL if it was open', async () => {
    const { calls, router } = await open(`?a=${aBaseline().id}`, [
      LIST,
      { path: DIFF_PATH, body: aDiff() },
      { method: 'DELETE', path: `/baselines/${aBaseline().id}`, status: 204 },
    ])

    await fireEvent.click(await screen.findByRole('button', { name: 'Supprimer' }))
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
    await fireEvent.click(screen.getByRole('button', { name: 'Confirmer' }))

    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true))
    await waitFor(() => expect(router.currentRoute.value.query.a).toBeUndefined())
  })

  it('opens the restoration dialog on the baseline chosen, and closes it on Annuler', async () => {
    const { calls } = await open('', [
      LIST,
      { method: 'POST', path: `/baselines/${aBaseline().id}/restore`, body: aRestore() },
    ])

    await fireEvent.click(await screen.findByRole('button', { name: 'Restaurer' }))

    expect(await screen.findByRole('dialog', { name: /Avant migration/ })).toBeTruthy()
    await waitFor(() =>
      expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ dry_run: true }),
    )
    await fireEvent.click(screen.getByRole('button', { name: 'Annuler' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('offers a reader no capture, no restoration and no deletion', async () => {
    asAReader()
    await open(`?a=${aBaseline().id}`, [LIST, { path: DIFF_PATH, body: aDiff() }])

    await screen.findByText('Avant migration')

    expect(screen.queryByRole('button', { name: 'Capturer' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Restaurer' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Supprimer' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Comparer au modèle courant' })).toBeTruthy()
  })
})
