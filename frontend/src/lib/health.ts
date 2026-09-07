// The API lives on its own origin, so this URL must be absolute: a relative
// '/health' would hit the Vite dev server, which answers 200 with index.html
// and makes the health check fail on a JSON parse instead of on the network.
// The default matches `make run-be`; override it with VITE_API_BASE_URL.
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(
  /\/+$/,
  '',
)

const HEALTH_URL = `${API_BASE_URL}/health`

export async function fetchHealth(): Promise<string> {
  const res = await fetch(HEALTH_URL)

  if (!res.ok) {
    throw new Error(`Health check failed with status ${res.status}`)
  }

  const data = await res.json()
  return data.status
}
