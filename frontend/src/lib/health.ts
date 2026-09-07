const HEALTH_URL = '/health'

export async function fetchHealth(): Promise<string> {
  const res = await fetch(HEALTH_URL)

  if (!res.ok) {
    throw new Error(`Health check failed with status ${res.status}`)
  }

  const data = await res.json()
  return data.status
}
