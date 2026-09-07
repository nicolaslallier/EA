// Liveness check, on the generated client like every other call.
//
// It used to hold its own `fetch` and its own base URL, as a stand-in until
// `npm run generate:api` existed (docs/adr/0002). It exists now, so the URL
// and the error handling live in `api.ts` and the response type comes from the
// backend's schema instead of being retyped here.
import { api, unwrap } from './api'

export async function fetchHealth(): Promise<string> {
  const { status } = unwrap(await api.GET('/health', {}))
  return status
}
