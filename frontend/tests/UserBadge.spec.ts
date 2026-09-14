import { fireEvent, render, screen } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import { computed, nextTick, ref } from 'vue'
import { createMemoryHistory } from 'vue-router'

import UserBadge from '../src/components/UserBadge.vue'
import { signOut } from '../src/lib/auth'
import { useMe } from '../src/lib/me'
import { createAppRouter } from '../src/router'
import { HOME } from '../src/router/sections'

async function renderAt(path: string) {
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  await router.isReady()
  return render(UserBadge, { global: { plugins: [router] } })
}

describe('UserBadge', () => {
  it('shows who is logged in, from useMe', async () => {
    vi.mocked(useMe).mockReturnValueOnce({
      me: ref({ username: 'alice', can_write: false }),
      canWrite: computed(() => false),
      error: ref(null),
      load: vi.fn(() => Promise.resolve()),
    })

    await renderAt(HOME)

    expect(screen.getByText('alice')).toBeInTheDocument()
  })

  it('asks the API for who is logged in once on a section', async () => {
    const { load } = useMe()
    vi.mocked(load).mockClear()

    await renderAt(HOME)

    expect(load).toHaveBeenCalled()
  })

  // Regression: the shell mounts on /auth/callback too, before the login has
  // stored a token. Asking then sent /me with no Authorization header, got a
  // 401 the reauth guard rightly would not retry, and the badge — and every
  // write control behind `canWrite` — stayed empty for good.
  it('does not ask before the login completes, and asks once it has', async () => {
    const { load } = useMe()
    vi.mocked(load).mockClear()
    const router = createAppRouter(createMemoryHistory())

    render(UserBadge, { global: { plugins: [router] } })
    await router.push('/auth/callback')
    await nextTick()
    expect(load).not.toHaveBeenCalled()

    await router.replace(HOME)
    await nextTick()
    expect(load).toHaveBeenCalled()
  })

  it('signs out when Déconnexion is clicked', async () => {
    await renderAt(HOME)

    await fireEvent.click(screen.getByRole('button', { name: 'Déconnexion' }))

    expect(signOut).toHaveBeenCalled()
  })
})
