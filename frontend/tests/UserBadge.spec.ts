import { fireEvent, render, screen } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'

import UserBadge from '../src/components/UserBadge.vue'
import { signOut } from '../src/lib/auth'
import { useMe } from '../src/lib/me'

describe('UserBadge', () => {
  it('shows who is logged in, from useMe', () => {
    vi.mocked(useMe).mockReturnValueOnce({
      me: ref({ username: 'alice', can_write: false }),
      canWrite: computed(() => false),
      error: ref(null),
      load: vi.fn(() => Promise.resolve()),
    })

    render(UserBadge)

    expect(screen.getByText('alice')).toBeInTheDocument()
  })

  it('asks the API for who is logged in once mounted', () => {
    const { load } = useMe()
    render(UserBadge)

    expect(load).toHaveBeenCalled()
  })

  it('signs out when Déconnexion is clicked', async () => {
    render(UserBadge)

    await fireEvent.click(screen.getByRole('button', { name: 'Déconnexion' }))

    expect(signOut).toHaveBeenCalled()
  })
})
