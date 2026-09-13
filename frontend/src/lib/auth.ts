// The login, by the Keycloak realm `ea` — authorization code + PKCE, the SPA
// being a public client. See docs/adr/0032.
//
// Tokens live in memory only (CLAUDE.md: never localStorage). A reload forgets
// them, the router's gate sends the page back to Keycloak, and Keycloak's own
// session cookie answers at once — a redirect, not a login form. The PKCE
// verifier has to survive that round trip, so the *state* goes to
// sessionStorage: it is single-use and holds no token.
import { InMemoryWebStorage, UserManager, WebStorageStateStore, type User } from 'oidc-client-ts'

import { createLogger } from './logging'

const log = createLogger('auth')

export const AUTH_AUTHORITY =
  import.meta.env.VITE_AUTH_AUTHORITY ?? 'https://keycloak.famillelallier.net/realms/ea'
export const AUTH_CLIENT_ID = import.meta.env.VITE_AUTH_CLIENT_ID ?? 'ea-spa'
export const CALLBACK_PATH = '/auth/callback'

export function createUserManager(origin: string): UserManager {
  return new UserManager({
    authority: AUTH_AUTHORITY,
    client_id: AUTH_CLIENT_ID,
    redirect_uri: `${origin}${CALLBACK_PATH}`,
    post_logout_redirect_uri: origin,
    response_type: 'code',
    scope: 'openid profile',
    userStore: new WebStorageStateStore({ store: new InMemoryWebStorage() }),
    stateStore: new WebStorageStateStore({ store: globalThis.sessionStorage }),
    automaticSilentRenew: true,
  })
}

const manager = createUserManager(globalThis.location?.origin ?? 'http://localhost:5173')

export async function accessToken(): Promise<string | null> {
  const user = await manager.getUser()
  return user && !user.expired ? user.access_token : null
}

/** Only an in-app path: a `returnTo` naming another origin would be an open redirect. */
export function safeReturnPath(value: unknown): string {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') ? value : '/'
}

// A page can fire several requests at once; each 401 would otherwise call
// `signinRedirect` on its own, each storing its own PKCE state and racing the
// others for `location.assign`. So only the first call actually starts one:
// later calls, while it is in flight, get the same promise back. It clears
// itself once that promise settles — in a real browser the page has by then
// navigated away and it never matters again; in a test the fake settles at
// once, so the next `it()` starts clean.
let redirecting: Promise<void> | null = null

export function signIn(returnTo: string): Promise<void> {
  redirecting ??= manager
    .signinRedirect({ state: { returnTo: safeReturnPath(returnTo) } })
    .finally(() => {
      redirecting = null
    })
  return redirecting
}

/** sessionStorage key for when a login last completed — a timestamp, never a token. */
const LAST_SIGNIN_KEY = 'ea.auth.signedInAt'

/**
 * How long after finishing a login a fresh 401 is treated as unrecoverable
 * rather than restarting it.
 *
 * Long enough that a request already in flight when the redirect began does
 * not read as this; short enough that a session genuinely expiring minutes
 * later still gets a retry. See `signInAfterUnauthorised`.
 */
export const REAUTH_GUARD_MS = 10_000

export async function completeSignIn(): Promise<string> {
  const user: User = await manager.signinRedirectCallback()
  sessionStorage.setItem(LAST_SIGNIN_KEY, String(Date.now()))
  return safeReturnPath((user.state as { returnTo?: unknown } | undefined)?.returnTo)
}

/**
 * What a 401 does: restart the login — unless one just finished, in which
 * case restarting would loop forever. That happens when Keycloak's session
 * cookie hands back a token the API still refuses (wrong audience or issuer,
 * clock skew, a backend that cannot reach Keycloak's signing keys): the
 * callback page calls the API, gets a 401, and would call this again with no
 * backoff, hitting Keycloak every time. Within the grace window this logs the
 * refusal instead and leaves it to the calling screen's own error state,
 * exactly as any other failed call.
 *
 * Returns whether a login was actually started, so a caller only interested
 * in that can tell the two cases apart without reading the log.
 */
export async function signInAfterUnauthorised(returnTo: string): Promise<boolean> {
  const signedInAt = Number(sessionStorage.getItem(LAST_SIGNIN_KEY) ?? 0)
  if (Date.now() - signedInAt < REAUTH_GUARD_MS) {
    log.error('the API refused a token Keycloak just issued — not restarting the login', {
      returnTo,
    })
    return false
  }
  await signIn(returnTo)
  return true
}

export function signOut(): Promise<void> {
  return manager.signoutRedirect()
}
