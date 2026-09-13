import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/vue'
import { afterEach, vi } from 'vitest'
import { computed, ref } from 'vue'

// Vitest runs with `globals: false`, so Testing Library cannot install its own
// auto-cleanup: without this, a second `render` stacks on top of the first and
// every query finds two of everything.
afterEach(cleanup)

// No spec may start a real redirect to Keycloak: jsdom cannot navigate, and
// the login flow is `auth.spec.ts`'s alone, which un-mocks this module.
vi.mock('../src/lib/auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/lib/auth')>()
  return {
    ...actual,
    accessToken: vi.fn(() => Promise.resolve('test-token')),
    signIn: vi.fn(() => Promise.resolve()),
    signInAfterUnauthorised: vi.fn(() => Promise.resolve(true)),
    signOut: vi.fn(() => Promise.resolve()),
    completeSignIn: vi.fn(() => Promise.resolve('/')),
  }
})

// Every spec sees a logged-in editor unless it says otherwise: the write
// controls this task adds would otherwise vanish from every existing spec
// that never heard of `lib/me`. `useMe` returns the *same* object on every
// call, like the real singleton, so a spec overriding one call with
// `vi.mocked(useMe).mockReturnValueOnce(...)` still sees the rest of the
// screen as an editor.
vi.mock('../src/lib/me', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/lib/me')>()
  const state = {
    me: ref<{ username: string; can_write: boolean } | null>({
      username: 'editor',
      can_write: true,
    }),
    canWrite: computed(() => true),
    error: ref<string | null>(null),
    load: vi.fn(() => Promise.resolve()),
  }
  return {
    ...actual,
    useMe: vi.fn(() => state),
  }
})
