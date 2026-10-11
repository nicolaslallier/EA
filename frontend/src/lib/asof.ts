// The day the whole catalogue is read on: `?asof=YYYY-MM-DD` in the URL, or
// nothing — "today, no filter". It is URL state like the depth of a traversal
// (docs/adr/0010): a link carries it, the back button undoes it, and changing it
// replaces the history entry instead of burying the page under dozens of dates.
// Which elements are in service that day is the API's to say (`as_of`,
// docs/adr/0060); this file only holds the day.
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const DAY = /^\d{4}-\d{2}-\d{2}$/

/** A real day written YYYY-MM-DD, or `undefined` — a hand-typed URL never reaches the API. */
export function parseAsOf(value: unknown): string | undefined {
  if (typeof value !== 'string' || !DAY.test(value)) {
    return undefined
  }
  const day = new Date(`${value}T00:00:00Z`)
  return Number.isNaN(day.getTime()) || day.toISOString().slice(0, 10) !== value ? undefined : value
}

export function useAsOf() {
  const route = useRoute()
  const router = useRouter()

  const asOf = computed(() => parseAsOf(route.query.asof))

  /** Read the catalogue on `day`; an empty or invalid day goes back to "today". */
  function setAsOf(day: string): void {
    const { asof: _dropped, ...rest } = route.query
    const valid = parseAsOf(day)
    void router.replace({ query: valid ? { ...rest, asof: valid } : rest })
  }

  return { asOf, setAsOf }
}
