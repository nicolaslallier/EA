import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import RestoreDialog from '../src/features/baselines/RestoreDialog.vue'
import { aBaseline, aRestore, deferApi, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const URL_OF = `/baselines/${aBaseline().id}/restore`
const NAME = 'Avant migration'

function mount() {
  return render(RestoreDialog, { props: { baseline: aBaseline() } })
}

const COUNTED = {
  elements_to_create: 1,
  elements_to_update: 2,
  elements_to_delete: 3,
  relationships_to_create: 4,
  relationships_to_delete: 5,
  relationships_cascaded: 7,
  boxes_to_restore: 6,
  documents_lost: 2,
  boxes_lost: 9,
}

function isDisabled(control: HTMLElement): boolean {
  return (control as HTMLButtonElement).disabled
}

function counted(label: string): string {
  const term = screen.getByText(label, { selector: 'dt' })
  return term.nextElementSibling?.textContent?.trim() ?? ''
}

describe('RestoreDialog — reading the plan', () => {
  it('asks for a dry run first and shows every count exactly', async () => {
    const calls = stubApi([{ method: 'POST', path: URL_OF, body: aRestore(COUNTED) }])
    mount()

    await screen.findByText('Éléments supprimés')

    expect(calls).toHaveLength(1)
    expect(calls[0]?.body).toEqual({ dry_run: true })
    expect(counted('Éléments recréés')).toBe('1')
    expect(counted('Éléments remis à l’état de la baseline')).toBe('2')
    expect(counted('Éléments supprimés')).toBe('3')
    expect(counted('Relations recréées')).toBe('4')
    expect(counted('Relations supprimées')).toBe('5')
    expect(counted('Relations supprimées avec leurs éléments')).toBe('7')
    expect(counted('Boîtes de diagramme remises en place')).toBe('6')
    expect(counted('Documents perdus')).toBe('2')
    expect(counted('Boîtes de diagramme perdues')).toBe('9')
  })

  it('says plainly that documents are not captured and what happens to those of a deleted element', async () => {
    stubApi([{ method: 'POST', path: URL_OF, body: aRestore({ documents_lost: 2 }) }])
    mount()

    expect(await screen.findByText(/documents ne sont pas capturés/i)).toBeTruthy()
    expect(screen.getByText(/ne reviendront pas/i)).toBeTruthy()
  })

  it('names the first elements of each kind and counts the rest', async () => {
    const ref = (label: string) => ({
      kind: 'element',
      id: label,
      label,
      type: 'node',
    })
    stubApi([
      {
        method: 'POST',
        path: URL_OF,
        body: aRestore({
          elements_to_delete: 25,
          deleted: [ref('alpha'), ref('<i>beta</i>')],
        }),
      },
    ])
    const { container } = mount()

    expect(await screen.findByText('alpha')).toBeTruthy()
    expect(screen.getByText('<i>beta</i>')).toBeTruthy()
    expect(container.querySelector('i')).toBeNull()
    expect(screen.getByText(/et 23 autres/)).toBeTruthy()
  })

  it('shows the warnings, among them the packages that went back to the root', async () => {
    stubApi([
      {
        method: 'POST',
        path: URL_OF,
        body: aRestore({
          warning_count: 3,
          warnings: ["element 'a': its package 'X' no longer exists, it is restored at the root"],
        }),
      },
    ])
    mount()

    expect(await screen.findByText(/restored at the root/)).toBeTruthy()
    expect(screen.getByText(/et 2 autres avertissements/)).toBeTruthy()
  })

  it('says it is computing, and a failed plan is an error with nothing to confirm', async () => {
    const calls = deferApi()
    mount()

    expect((await screen.findByRole('status')).textContent).toContain('Calcul')
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    calls[0]?.answer({ error: 'internal_error', detail: 'boom' }, 500)
    expect((await screen.findByRole('alert')).textContent).toContain('boom')
    expect(screen.queryByLabelText(/Saisir le nom/)).toBeNull()
    expect(screen.queryByRole('status')).toBeNull()
  })
})

describe('RestoreDialog — confirming', () => {
  it('applies only once the exact name has been typed, with the digest that was read', async () => {
    const calls = deferApi()
    const view = mount()
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    calls[0]?.answer(aRestore(COUNTED, { digest: 'd'.repeat(64) }))
    await screen.findByLabelText(/Saisir le nom/)
    const button = screen.getByRole('button', { name: 'Restaurer' })

    expect(isDisabled(button)).toBe(true)
    await fireEvent.update(screen.getByLabelText(/Saisir le nom/), 'Avant')
    expect(isDisabled(button)).toBe(true)
    await fireEvent.update(screen.getByLabelText(/Saisir le nom/), NAME)
    expect(isDisabled(button)).toBe(false)
    await fireEvent.click(button)

    await vi.waitFor(() => expect(calls).toHaveLength(2))
    expect(calls[1]?.method).toBe('POST')
    calls[1]?.answer(aRestore(COUNTED, { applied: true }))
    await waitFor(() => expect(view.emitted().restored).toBeTruthy())
  })

  it('sends the digest back and nothing else that could change the plan', async () => {
    const bodies: unknown[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const request = input instanceof Request ? input : new Request(input, init)
        bodies.push(await request.clone().json())
        return new Response(JSON.stringify(aRestore(COUNTED, { digest: 'd'.repeat(64) })), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }),
    )
    mount()

    await fireEvent.update(await screen.findByLabelText(/Saisir le nom/), NAME)
    await fireEvent.click(screen.getByRole('button', { name: 'Restaurer' }))

    await vi.waitFor(() => expect(bodies).toHaveLength(2))
    expect(bodies[1]).toEqual({
      dry_run: false,
      expected_digest: 'd'.repeat(64),
    })
  })

  it('refuses to apply while a protected element is in the way, and lists it', async () => {
    stubApi([
      {
        method: 'POST',
        path: URL_OF,
        body: aRestore({
          blocked_count: 2,
          blocked: [{ kind: 'element', id: 'x', label: 'billing', type: 'node' }],
        }),
      },
    ])
    mount()

    expect(await screen.findByText('billing')).toBeTruthy()
    expect(screen.getByText(/et 1 autre/)).toBeTruthy()
    await fireEvent.update(screen.getByLabelText(/Saisir le nom/), NAME)
    expect(isDisabled(screen.getByRole('button', { name: 'Restaurer' }))).toBe(true)
  })

  it('does not send twice while the first answer is awaited', async () => {
    const calls = deferApi()
    mount()
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    calls[0]?.answer(aRestore(COUNTED))
    await fireEvent.update(await screen.findByLabelText(/Saisir le nom/), NAME)
    const button = screen.getByRole('button', { name: 'Restaurer' })

    await fireEvent.click(button)
    await fireEvent.click(button)
    await fireEvent.click(button)

    await vi.waitFor(() => expect(calls).toHaveLength(2))
    expect(isDisabled(button)).toBe(true)
    expect(calls).toHaveLength(2)
  })

  it('reads the plan again when the model moved, and asks to confirm again', async () => {
    const calls = deferApi()
    const view = mount()
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    calls[0]?.answer(aRestore(COUNTED, { digest: '1'.repeat(64) }))
    await fireEvent.update(await screen.findByLabelText(/Saisir le nom/), NAME)
    await fireEvent.click(screen.getByRole('button', { name: 'Restaurer' }))
    await vi.waitFor(() => expect(calls).toHaveLength(2))

    calls[1]?.answer({ error: 'restore_conflict', detail: 'the model changed' }, 409)

    expect((await screen.findByRole('alert')).textContent).toContain('a changé')
    await vi.waitFor(() => expect(calls).toHaveLength(3)) // the plan is read again
    calls[2]?.answer(aRestore({ ...COUNTED, elements_to_delete: 8 }, { digest: '2'.repeat(64) }))
    await waitFor(() => expect(counted('Éléments supprimés')).toBe('8'))
    expect(screen.getByLabelText<HTMLInputElement>(/Saisir le nom/).value).toBe('')
    expect(isDisabled(screen.getByRole('button', { name: 'Restaurer' }))).toBe(true)
    expect(view.emitted().restored).toBeUndefined()
    expect(view.emitted().close).toBeUndefined()
  })

  it('explains a protected element in words and keeps the dialog open', async () => {
    const calls = deferApi()
    const view = mount()
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    calls[0]?.answer(aRestore(COUNTED))
    await fireEvent.update(await screen.findByLabelText(/Saisir le nom/), NAME)
    await fireEvent.click(screen.getByRole('button', { name: 'Restaurer' }))
    await vi.waitFor(() => expect(calls).toHaveLength(2))

    calls[1]?.answer({ error: 'restore_blocked', detail: '1 protected element(s)' }, 409)

    expect((await screen.findByRole('alert')).textContent).toContain('verrouillé')
    expect(view.emitted().restored).toBeUndefined()
    expect(screen.getByRole('dialog')).toBeTruthy()
  })

  it('keeps the dialog open, with the button usable again, after any other failure', async () => {
    const calls = deferApi()
    const view = mount()
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    calls[0]?.answer(aRestore(COUNTED))
    await fireEvent.update(await screen.findByLabelText(/Saisir le nom/), NAME)
    await fireEvent.click(screen.getByRole('button', { name: 'Restaurer' }))
    await vi.waitFor(() => expect(calls).toHaveLength(2))

    calls[1]?.answer({ error: 'internal_error', detail: 'boom' }, 500)

    expect((await screen.findByRole('alert')).textContent).toContain('boom')
    expect(isDisabled(screen.getByRole('button', { name: 'Restaurer' }))).toBe(false)
    expect(view.emitted().restored).toBeUndefined()
  })
})

describe('RestoreDialog — keyboard and focus', () => {
  it('is a modal dialog named after the baseline, and takes the focus', async () => {
    stubApi([{ method: 'POST', path: URL_OF, body: aRestore(COUNTED) }])
    mount()

    const dialog = screen.getByRole('dialog', {
      name: /Restaurer « Avant migration »/,
    })

    expect(dialog.getAttribute('aria-modal')).toBe('true')
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true))
  })

  it('closes on Escape and on Annuler, but not while a restoration is in flight', async () => {
    const calls = deferApi()
    const view = mount()
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    calls[0]?.answer(aRestore(COUNTED))
    await fireEvent.update(await screen.findByLabelText(/Saisir le nom/), NAME)
    await fireEvent.click(screen.getByRole('button', { name: 'Restaurer' }))
    await vi.waitFor(() => expect(calls).toHaveLength(2))

    await fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(view.emitted().close).toBeUndefined()
    expect(isDisabled(screen.getByRole('button', { name: 'Annuler' }))).toBe(true)

    calls[1]?.answer({ error: 'internal_error', detail: 'boom' }, 500)
    await screen.findByRole('alert')
    await fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(view.emitted().close).toHaveLength(1)
  })

  it('keeps Tab inside the dialog, both ways round', async () => {
    stubApi([{ method: 'POST', path: URL_OF, body: aRestore(COUNTED) }])
    mount()
    await screen.findByLabelText(/Saisir le nom/)
    const dialog = screen.getByRole('dialog')
    const focusable = within(dialog).getAllByRole('button')
    const first = dialog.querySelector<HTMLElement>('input, button:not(:disabled)')
    const last = focusable.filter((b) => !(b as HTMLButtonElement).disabled).at(-1)

    last?.focus()
    await fireEvent.keyDown(last as HTMLElement, { key: 'Tab' })
    expect(document.activeElement).toBe(first)

    first?.focus()
    await fireEvent.keyDown(first as HTMLElement, {
      key: 'Tab',
      shiftKey: true,
    })
    expect(document.activeElement).toBe(last)
  })

  it('gives the focus back to whoever opened it', async () => {
    stubApi([{ method: 'POST', path: URL_OF, body: aRestore(COUNTED) }])
    const opener = document.createElement('button')
    document.body.append(opener)
    opener.focus()
    const view = mount()
    await screen.findByLabelText(/Saisir le nom/)

    view.unmount()

    expect(document.activeElement).toBe(opener)
    opener.remove()
  })
})
