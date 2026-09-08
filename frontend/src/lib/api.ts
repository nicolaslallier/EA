// The typed client every call goes through.
//
// `src/api/schema.d.ts` is generated from the backend's OpenAPI document
// (`make openapi`) and must never be edited; this module is the hand-written
// half — the base URL and the error handling, neither of which the generator
// knows about. See docs/adr/0007.
import createClient from 'openapi-fetch'

import type { paths } from '../api/schema'

/** The port `make run-be` serves the API on. */
const API_PORT = 8000

/** The two fields of `window.location` this module needs — all a test must fake. */
type Origin = { protocol: string; hostname: string }

/**
 * The API's address when nothing configured one: this page's host, port 8000.
 *
 * Both servers bind every interface (docs/adr/0016 for the API, 0019 for Vite),
 * so the SPA is loaded from `http://192.168.1.x:5173` as readily as from
 * localhost. A constant `http://localhost:8000` would then name the *viewer's*
 * machine — which usually runs no backend at all — so the host is taken from
 * wherever the page itself came from, and the scheme with it, so a page served
 * over TLS never falls back to plain HTTP.
 */
export function defaultApiBaseUrl(origin: Origin | undefined): string {
  if (origin === undefined) {
    return `http://localhost:${API_PORT}`
  }
  return `${origin.protocol}//${origin.hostname}:${API_PORT}`
}

// The API lives on its own origin, so this URL must be absolute: a relative
// '/elements' would hit the Vite dev server, which answers 200 with index.html
// and turns a failure into a confusing JSON parse error.
// Override it with VITE_API_BASE_URL whenever the backend is neither on this
// host nor on that port.
export const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ?? defaultApiBaseUrl(globalThis.location)
).replace(/\/+$/, '')

export const api = createClient<paths>({
  baseUrl: API_BASE_URL,
  // Resolve `fetch` per call instead of letting the client capture it at
  // creation: captured once, a test that stubs `globalThis.fetch` would still
  // reach the real backend — which, the graph being shared, means writing to
  // everyone's data from a unit test.
  fetch: (request) => globalThis.fetch(request),
})

/** A failure the API chose to describe — never a stack trace, by design. */
export class ApiError extends Error {
  // Fields are declared and assigned rather than being constructor parameter
  // properties: `erasableSyntaxOnly` rules out syntax that emits code.
  readonly code: string
  readonly status: number

  constructor(message: string, code: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }
}

/** What a caller shows the user when `fetch` itself never reached the backend. */
export const UNREACHABLE = 'Backend injoignable — as-tu lancé `make run-be` ?'

type Envelope = { error?: unknown; detail?: unknown }
type ValidationItem = { loc?: unknown[]; msg?: unknown }

/**
 * Turn a failed response body into one line for a human.
 *
 * Two shapes arrive here: the API's own `{error, detail}` envelope, and
 * FastAPI's validation report, whose `detail` is a list of field errors. A
 * client that only understood the first would show `[object Object]` on every
 * rejected form.
 */
function describe(body: unknown, status: number): string {
  const envelope = (body ?? {}) as Envelope
  if (typeof envelope.detail === 'string') {
    return envelope.detail
  }
  if (Array.isArray(envelope.detail)) {
    const fields = envelope.detail
      .map((item: ValidationItem) => {
        const field = (item.loc ?? []).filter((part) => part !== 'body').join('.')
        return field ? `${field} : ${item.msg}` : String(item.msg)
      })
      .filter(Boolean)
    if (fields.length > 0) {
      return fields.join(' ; ')
    }
  }
  return `Le backend a répondu ${status}.`
}

function codeOf(body: unknown): string {
  const envelope = (body ?? {}) as Envelope
  return typeof envelope.error === 'string' ? envelope.error : 'error'
}

/**
 * Unwrap an `openapi-fetch` result, raising `ApiError` on anything but success.
 *
 * The generated client reports a failure in the returned object rather than by
 * throwing, so without this every call site would have to remember to look at
 * `error` — and one that forgot would silently render `undefined`.
 */
export function unwrap<T>(result: {
  data?: T
  error?: unknown
  response: Response
}): T {
  if (result.error !== undefined || !result.response.ok) {
    throw new ApiError(
      describe(result.error, result.response.status),
      codeOf(result.error),
      result.response.status,
    )
  }
  return result.data as T
}

/** The message to display for anything thrown by a call, network faults included. */
export function messageOf(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message
  }
  return UNREACHABLE
}
