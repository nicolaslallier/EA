// The audit trail (docs/adr/0044): one page at a time, the page named by the URL.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import type { AuditAction, EntityKind } from './auditLabels'

export type AuditEvent = components['schemas']['AuditEventRead']

export type AuditFilters = {
  kind?: EntityKind
  entity?: string
  actor?: string
  action?: AuditAction
  since?: string
  cursor?: string
}

/** An empty box in a form is "no filter", not a filter on the empty string. */
function given<T extends string>(value: T | undefined): T | undefined {
  return value ? value : undefined
}

export function useAuditLog() {
  const events = ref<AuditEvent[]>([])
  const nextCursor = ref<string | null>(null)
  // The URL changes faster than the network answers: the page on screen must be
  // the page the URL names now.
  const request = useLatestRequest()
  const { status, error } = request

  async function load(filters: AuditFilters, limit?: number): Promise<void> {
    await request.run(
      async (signal) =>
        unwrap(
          await api.GET('/audit', {
            params: {
              query: {
                entity_kind: given(filters.kind),
                entity_id: given(filters.entity),
                actor: given(filters.actor),
                action: given(filters.action),
                since: given(filters.since),
                cursor: given(filters.cursor),
                limit,
              },
            },
            signal,
          }),
        ),
      (page) => {
        events.value = page.items
        nextCursor.value = page.next_cursor
      },
      () => {
        events.value = []
        nextCursor.value = null
      },
    )
  }

  return { events, nextCursor, status, error, load }
}
