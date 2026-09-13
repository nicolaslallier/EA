// The router is built from `SECTIONS`, not from a second hand-kept list: a
// section without a `view` gets no route, so a link can never resolve to a
// screen that does not exist.
import type { RouteRecordRaw, Router, RouterHistory } from 'vue-router'
import { createRouter, createWebHistory } from 'vue-router'

import { accessToken, CALLBACK_PATH, signIn } from '../lib/auth'
import NotFound from './NotFound.vue'
import { HOME, isBuilt, SECTIONS } from './sections'

export type Gate = {
  accessToken: () => Promise<string | null>
  signIn: (returnTo: string) => Promise<void>
}

export const routes: RouteRecordRaw[] = [
  { path: '/', redirect: HOME },
  ...SECTIONS.filter(isBuilt).map((section) => ({
    path: section.path,
    name: section.name,
    component: section.view,
    meta: { label: section.label },
  })),
  {
    path: CALLBACK_PATH,
    name: 'auth-callback',
    component: () => import('./AuthCallback.vue'),
    meta: { public: true },
  },
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
  // do; this only decides that they log in first (docs/adr/0031).
  router.beforeEach(async (to) => {
    if (to.meta.public || (await gate.accessToken())) {
      return true
    }
    await gate.signIn(to.fullPath)
    return false
  })
  return router
}
