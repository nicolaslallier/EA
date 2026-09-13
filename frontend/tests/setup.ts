import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/vue'
import { afterEach, vi } from 'vitest'

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
    signOut: vi.fn(() => Promise.resolve()),
    completeSignIn: vi.fn(() => Promise.resolve('/')),
  }
})

