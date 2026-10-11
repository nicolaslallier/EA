import { afterEach, describe, expect, it, vi } from 'vitest'

import { useApproval } from '../src/features/elements/useApproval'
import { anElement, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useApproval', () => {
  it('posts the transition under the name the backend reads and returns the element', async () => {
    const element = anElement({
      approval_state: 'in_review',
      allowed_transitions: [],
    })
    const calls = stubApi([
      {
        method: 'POST',
        path: `/elements/${element.id}/approval`,
        body: element,
      },
    ])

    const answer = await useApproval().transition(element.id, 'submit')

    expect(calls[0]?.body).toEqual({ transition: 'submit' })
    expect(answer.approval_state).toBe('in_review')
  })

  it('names the revision the approver read, on an approval only', async () => {
    const element = anElement({ approval_state: 'in_review' })
    const calls = stubApi([
      {
        method: 'POST',
        path: `/elements/${element.id}/approval`,
        body: element,
      },
    ])

    await useApproval().transition(element.id, 'approve', 7)
    await useApproval().transition(element.id, 'reject', 7)

    expect(calls[0]?.body).toEqual({
      transition: 'approve',
      expected_revision: 7,
    })
    expect(calls[1]?.body).toEqual({ transition: 'reject' })
  })
})
