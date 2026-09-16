// Liveness check, on the generated client like every other call.
//
// It used to hold its own `fetch` and its own base URL, as a stand-in until
// `npm run generate:api` existed (docs/adr/0002). It exists now, so the URL
// and the error handling live in `api.ts` and the response type comes from the
// backend's schema instead of being retyped here.
//
// Since docs/adr/0037 the answer carries a second field: the API boots without
// a store that serves one section, so "reachable" and "whole" are two
// questions and `/health` answers both.
import type { components } from '../api/schema'

import { api, unwrap } from './api'

// The generated shape, with its optional field made certain — `Required<>`
// rather than a hand-written interface, so this still derives from the schema
// and never becomes a second description of the payload.
export type Health = Required<components['schemas']['HealthResponse']>

export async function fetchHealth(): Promise<Health> {
  const { status, degraded } = unwrap(await api.GET('/health', {}))
  // `degraded` is optional in the schema because it has a default server-side;
  // every caller here wants a list it can map over.
  return { status, degraded: degraded ?? [] }
}
