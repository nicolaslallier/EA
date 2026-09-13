// The login, by the Keycloak realm `ea` — authorization code + PKCE, the SPA
// being a public client. See docs/adr/0031.
//
// Tokens live in memory only (CLAUDE.md: never localStorage). A reload forgets
// them, the router's gate sends the page back to Keycloak, and Keycloak's own
// session cookie answers at once — a redirect, not a login form. The PKCE
// verifier has to survive that round trip, so the *state* goes to
// sessionStorage: it is single-use and holds no token.
import { InMemoryWebStorage, UserManager, WebStorageStateStore, type User } from 'oidc-client-ts'

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

export function signIn(returnTo: string): Promise<void> {
  return manager.signinRedirect({ state: { returnTo: safeReturnPath(returnTo) } })
}

export async function completeSignIn(): Promise<string> {
  const user: User = await manager.signinRedirectCallback()
  return safeReturnPath((user.state as { returnTo?: unknown } | undefined)?.returnTo)
}

export function signOut(): Promise<void> {
  return manager.signoutRedirect()
}
