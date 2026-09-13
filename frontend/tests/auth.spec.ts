import { InMemoryWebStorage, UserManager, type User } from 'oidc-client-ts'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.unmock('../src/lib/auth')

const { CALLBACK_PATH, completeSignIn, createUserManager, REAUTH_GUARD_MS, safeReturnPath, signInAfterUnauthorised } =
  await import('../src/lib/auth')

describe('the login client', () => {
  it('returns to this origin after Keycloak', () => {
    const manager = createUserManager('http://192.168.1.50:5173')
    expect(manager.settings.redirect_uri).toBe(`http://192.168.1.50:5173${CALLBACK_PATH}`)
    expect(manager.settings.response_type).toBe('code')
    expect(manager.settings.client_id).toBe('ea-spa')
  })

  it('keeps the tokens in memory, never in localStorage', () => {
    const manager = createUserManager('http://localhost:5173')
    // `userStore` is a WebStorageStateStore; its backing store must be the in-memory one.
    const store = (manager.settings.userStore as unknown as { _store: unknown })._store
    expect(store).toBeInstanceOf(InMemoryWebStorage)
  })
})

describe('where a login returns', () => {
  it.each([
    ['/elements?element=42', '/elements?element=42'],
    ['https://evil.example/', '/'],
    ['//evil.example/', '/'],
    [undefined, '/'],
    [42, '/'],
  ])('%s → %s', (value, expected) => {
    expect(safeReturnPath(value)).toBe(expected)
  })
})

describe('what a 401 does about the login', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
    sessionStorage.clear()
  })

  /** Record a completed login, the way `completeSignIn` itself does. */
  async function signInJustNow(): Promise<void> {
    vi.spyOn(UserManager.prototype, 'signinRedirectCallback').mockResolvedValue({
      state: { returnTo: '/elements' },
    } as User)
    await completeSignIn()
    vi.restoreAllMocks()
  }

  it('starts only one redirect when several requests are refused at once', async () => {
    const redirect = vi.spyOn(UserManager.prototype, 'signinRedirect').mockResolvedValue(undefined)

    await Promise.all([
      signInAfterUnauthorised('/a'),
      signInAfterUnauthorised('/b'),
      signInAfterUnauthorised('/c'),
    ])

    expect(redirect).toHaveBeenCalledTimes(1)
  })

  it('does not restart the login right after one just completed — Keycloak would only hand back the same refused token', async () => {
    await signInJustNow()
    const redirect = vi.spyOn(UserManager.prototype, 'signinRedirect').mockResolvedValue(undefined)
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})

    const started = await signInAfterUnauthorised('/elements')

    expect(started).toBe(false)
    expect(redirect).not.toHaveBeenCalled()
    expect(error).toHaveBeenCalled()
  })

  it('redirects again once the grace window has passed', async () => {
    await signInJustNow()
    const redirect = vi.spyOn(UserManager.prototype, 'signinRedirect').mockResolvedValue(undefined)
    vi.useFakeTimers()
    vi.setSystemTime(Date.now() + REAUTH_GUARD_MS + 1)

    const started = await signInAfterUnauthorised('/elements')

    expect(started).toBe(true)
    expect(redirect).toHaveBeenCalledTimes(1)
  })
})
