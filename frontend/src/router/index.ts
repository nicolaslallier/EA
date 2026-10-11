// The router is built from `SECTIONS`, not from a second hand-kept list: a
// section without a `view` gets no route, so a link can never resolve to a
// screen that does not exist.
import type { RouteRecordRaw, Router, RouterHistory } from 'vue-router'
import { createRouter, createWebHistory } from 'vue-router'

import { parseAsOf } from '../lib/asof'
import { accessToken, CALLBACK_PATH, signIn } from '../lib/auth'
import { createLogger } from '../lib/logging'
import LoginFailed from './LoginFailed.vue'
import NotFound from './NotFound.vue'
import { HOME, isBuilt, SECTIONS } from './sections'

const log = createLogger('auth')

export type Gate = {
  accessToken: () => Promise<string | null>
  signIn: (returnTo: string) => Promise<void>
}

export const routes: RouteRecordRaw[] = [
  { path: '/', redirect: HOME },
  ...SECTIONS.filter(isBuilt).map(
    (section) =>
      ({
        path: section.path,
        name: section.name,
        component: section.view,
        // A section with screens nested inside it (the reader mode) mounts them here.
        children: section.children,
        meta: { label: section.label, reader: section.reader === true },
      }) as RouteRecordRaw,
  ),
  {
    path: CALLBACK_PATH,
    name: 'auth-callback',
    component: () => import('./AuthCallback.vue'),
    meta: { public: true },
  },
  // Imported eagerly, like NotFound: the page for a login that could not start
  // must not itself depend on fetching a chunk.
  { path: '/auth/failed', name: 'login-failed', component: LoginFailed, meta: { public: true } },
  { path: '/:pathMatch(.*)*', name: 'not-found', component: NotFound },
]

/**
 * Build the router. The history is injected so the tests can run on
 * `createMemoryHistory()` without touching `window.location`; the gate is
 * injected the same way, so a test can deny a visitor a token without a real
 * Keycloak redirect.
 */
export function createAppRouter(
  history: RouterHistory = createWebHistory(),
  gate: Gate = { accessToken, signIn },
): Router {
  const router = createRouter({ history, routes })
  // Nobody reaches a section without a token. The API decides what they may
  // do; this only decides that they log in first (docs/adr/0032).
  router.beforeEach(async (to) => {
    if (to.meta.public || (await gate.accessToken())) {
      return true
    }
    try {
      await gate.signIn(to.fullPath)
    } catch (error) {
      // Keycloak unreachable, its metadata unreadable, or a page on plain http
      // where the browser withholds the crypto PKCE needs: an uncaught
      // rejection here would abort the navigation and render nothing.
      log.error('the login could not start', {
        returnTo: to.fullPath,
        reason: error instanceof Error ? error.message : String(error),
      })
      return { name: 'login-failed', query: { returnTo: to.fullPath } }
    }
    return false
  })
  // The date the catalogue is read on follows the user from section to section
  // (docs/adr/0060): the menu and every internal link land on the next section
  // with `?asof=` still on. Only a change of path carries it, so clearing the
  // date on the page you are on stays cleared.
  router.beforeEach((to, from) => {
    const kept = parseAsOf(from.query.asof)
    if (kept && to.query.asof === undefined && to.path !== from.path && !to.meta.public) {
      return { ...to, query: { ...to.query, asof: kept }, replace: true }
    }
    return true
  })
  return router
}
