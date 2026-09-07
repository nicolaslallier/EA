// The router is built from `SECTIONS`, not from a second hand-kept list: a
// section without a `view` gets no route, so a link can never resolve to a
// screen that does not exist.
import type { RouteRecordRaw, Router, RouterHistory } from 'vue-router'
import { createRouter, createWebHistory } from 'vue-router'

import NotFound from './NotFound.vue'
import { HOME, isBuilt, SECTIONS } from './sections'

export const routes: RouteRecordRaw[] = [
  { path: '/', redirect: HOME },
  ...SECTIONS.filter(isBuilt).map((section) => ({
    path: section.path,
    name: section.name,
    component: section.view,
    meta: { label: section.label },
  })),
  { path: '/:pathMatch(.*)*', name: 'not-found', component: NotFound },
]

/**
 * Build the router. The history is injected so the tests can run on
 * `createMemoryHistory()` without touching `window.location`.
 */
export function createAppRouter(history: RouterHistory = createWebHistory()): Router {
  return createRouter({ history, routes })
}
