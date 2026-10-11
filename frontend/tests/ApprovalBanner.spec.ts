import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ApprovalBanner from '../src/features/elements/ApprovalBanner.vue'
import { anElement, deferApi, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ApprovalBanner', () => {
  it('names the state, the revision and offers exactly the transitions the API listed', () => {
    render(ApprovalBanner, {
      props: {
        element: anElement({
          approval_state: 'in_review',
          revision: 4,
          allowed_transitions: ['approve', 'reject'],
        }),
      },
    })

    expect(screen.getByText('En revue')).toBeTruthy()
    expect(screen.getByText(/révision 4/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Approuver' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Rejeter' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Verrouiller' })).toBeNull()
  })

  it('shows no button at all when the caller may do nothing', () => {
    render(ApprovalBanner, {
      props: { element: anElement({ allowed_transitions: [] }) },
    })

    expect(screen.queryAllByRole('button')).toHaveLength(0)
  })

  it('shows a locked element as locked, who approved it and when, and why it will not change', () => {
    render(ApprovalBanner, {
      props: {
        element: anElement({
          approval_state: 'approved',
          locked: true,
          approved_by: 'approver-1',
          approved_at: '2026-10-09T10:00:00Z',
          allowed_transitions: ['unlock'],
        }),
      },
    })

    expect(screen.getByText('Verrouillé')).toBeTruthy()
    expect(screen.getByText(/approver-1/)).toBeTruthy()
    expect(screen.getByText(/2026-10-09/)).toBeTruthy()
    expect(screen.getByText(/aucune modification/i)).toBeTruthy()
  })

  it('explains that an approved element is only changed by an approver', () => {
    render(ApprovalBanner, {
      props: {
        element: anElement({
          approval_state: 'approved',
          allowed_transitions: [],
        }),
      },
    })

    expect(screen.getByText(/seul un approbateur/i)).toBeTruthy()
  })

  it('posts the transition and announces the element it comes back with', async () => {
    const before = anElement({ allowed_transitions: ['submit'] })
    const after = anElement({
      approval_state: 'in_review',
      allowed_transitions: [],
    })
    const calls = stubApi([
      { method: 'POST', path: `/elements/${before.id}/approval`, body: after },
    ])
    const view = render(ApprovalBanner, { props: { element: before } })

    await fireEvent.click(screen.getByRole('button', { name: 'Soumettre à revue' }))

    await waitFor(() => expect(view.emitted().changed).toBeTruthy())
    expect(view.emitted().changed?.[0]).toEqual([after])
    expect(calls[0]?.body).toEqual({ transition: 'submit' })
  })

  it('approves the revision it was loaded with', async () => {
    const before = anElement({
      approval_state: 'in_review',
      revision: 3,
      allowed_transitions: ['approve'],
    })
    const calls = stubApi([
      { method: 'POST', path: `/elements/${before.id}/approval`, body: before },
    ])
    render(ApprovalBanner, { props: { element: before } })

    await fireEvent.click(screen.getByRole('button', { name: 'Approuver' }))

    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0]?.body).toEqual({
      transition: 'approve',
      expected_revision: 3,
    })
  })

  it('disables the buttons while the call is out', async () => {
    const before = anElement({ allowed_transitions: ['submit'] })
    const held = deferApi()
    render(ApprovalBanner, { props: { element: before } })

    await fireEvent.click(screen.getByRole('button', { name: 'Soumettre à revue' }))

    await waitFor(() => expect(held).toHaveLength(1))
    expect(screen.getByRole('button', { name: 'Soumettre à revue' })).toBeDisabled()
    held[0]?.answer(anElement({ approval_state: 'in_review' }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Soumettre à revue' })).not.toBeDisabled(),
    )
  })

  it('shows the API refusal instead of swallowing it', async () => {
    const before = anElement({
      approval_state: 'in_review',
      allowed_transitions: ['approve'],
    })
    stubApi([
      {
        method: 'POST',
        path: `/elements/${before.id}/approval`,
        status: 403,
        body: { error: 'forbidden', detail: 'separation of duties' },
      },
    ])
    render(ApprovalBanner, { props: { element: before } })

    await fireEvent.click(screen.getByRole('button', { name: 'Approuver' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/separation of duties/)
  })

  it('on a stale approval, reads the element again, says so, and does not approve again', async () => {
    const before = anElement({
      approval_state: 'in_review',
      revision: 3,
      allowed_transitions: ['approve'],
    })
    const fresh = anElement({
      approval_state: 'in_review',
      revision: 4,
      allowed_transitions: ['approve'],
    })
    const calls = stubApi([
      {
        method: 'POST',
        path: `/elements/${before.id}/approval`,
        status: 409,
        body: { error: 'approval_stale', detail: 'revision 4, not 3' },
      },
      { path: `/elements/${before.id}`, body: fresh },
    ])
    const view = render(ApprovalBanner, { props: { element: before } })

    await fireEvent.click(screen.getByRole('button', { name: 'Approuver' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/contenu a changé/i)
    await waitFor(() => expect(view.emitted().changed?.[0]).toEqual([fresh]))
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(1)
  })

  it('announces the state politely and gives the focus back to the banner after a transition', async () => {
    const before = anElement({ allowed_transitions: ['submit'] })
    const after = anElement({ approval_state: 'in_review', allowed_transitions: [] })
    stubApi([{ method: 'POST', path: `/elements/${before.id}/approval`, body: after }])
    const view = render(ApprovalBanner, { props: { element: before } })

    expect(screen.getByText('Brouillon').getAttribute('aria-live')).toBe('polite')
    await fireEvent.click(screen.getByRole('button', { name: 'Soumettre à revue' }))

    await waitFor(() => expect(view.emitted().changed).toBeTruthy())
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole('region', { name: "État d'approbation" }),
      ),
    )
  })
})
