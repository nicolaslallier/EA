// What one element cites in other tools: the listing (from the server's cache), the targets the
// deployment declared, and the writes. Nothing here knows how an identifier is spelled or what
// a provider accepts — the server decides, and says why in words (docs/adr/0062).
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'

export type ExternalReference = components['schemas']['ExternalReferenceRead']
export type ExternalTarget = components['schemas']['ExternalTargetRead']
export type ExternalIssue = components['schemas']['ExternalIssueRead']
export type ExternalKind = components['schemas']['ExternalKind']
export type ExternalStatus = components['schemas']['ExternalStatus']

// Translation tables, not rules: a kind or a state with no entry is shown as it came.
export const KIND_LABELS: Record<string, string> = {
  url: 'Lien',
  github_issue: 'Ticket GitHub',
  jira_issue: 'Ticket Jira',
  confluence_page: 'Page Confluence',
  servicenow_ticket: 'Ticket ServiceNow',
}

export const STATUS_LABELS: Record<string, string> = {
  open: 'ouvert',
  closed: 'fermé',
  unknown: 'inconnu',
}

/**
 * `href` only for an http(s) address, else nothing: the server builds the address of a resolved
 * reference, but a free link is a person's text, and `javascript:` must never become a link.
 */
export function safeHref(url: string): string | undefined {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : undefined
  } catch {
    return undefined
  }
}

const RELATIVE = new Intl.RelativeTimeFormat('fr', { numeric: 'auto' })

/** « il y a 3 heures » for a past instant; empty when the cache was never filled. */
export function ago(iso: string | null, now: Date = new Date()): string {
  if (!iso) {
    return ''
  }
  const seconds = Math.round((new Date(iso).getTime() - now.getTime()) / 1000)
  const steps: [Intl.RelativeTimeFormatUnit, number][] = [
    ['day', 86_400],
    ['hour', 3_600],
    ['minute', 60],
  ]
  for (const [unit, size] of steps) {
    if (Math.abs(seconds) >= size) {
      return RELATIVE.format(Math.round(seconds / size), unit)
    }
  }
  return RELATIVE.format(0, 'second')
}

export function useExternalReferences() {
  const items = ref<ExternalReference[]>([])
  const targets = ref<ExternalTarget[]>([])
  const listing = useLatestRequest()
  const { status, error } = listing
  const known = useLatestRequest()

  /** The targets first: by the time the list shows, the form knows what it may offer. */
  async function load(elementId: string): Promise<void> {
    await known.run(
      async (signal) => unwrap(await api.GET('/external/targets', { signal })),
      (answer) => {
        targets.value = answer
      },
    )
    await listing.run(
      async (signal) =>
        unwrap(
          await api.GET('/elements/{element_id}/external-references', {
            params: { path: { element_id: elementId } },
            signal,
          }),
        ),
      (answer) => {
        items.value = answer
      },
    )
  }

  async function link(
    elementId: string,
    kind: ExternalKind,
    reference: string,
    target: string | null,
  ): Promise<void> {
    unwrap(
      await api.POST('/elements/{element_id}/external-references', {
        params: { path: { element_id: elementId } },
        body: { kind, reference, target },
      }),
    )
    await load(elementId)
  }

  async function refresh(elementId: string, referenceId: string): Promise<void> {
    unwrap(
      await api.POST('/external-references/{reference_id}/refresh', {
        params: { path: { reference_id: referenceId } },
      }),
    )
    await load(elementId)
  }

  async function refreshAll(elementId: string): Promise<void> {
    unwrap(
      await api.POST('/elements/{element_id}/external-references/refresh', {
        params: { path: { element_id: elementId } },
      }),
    )
    await load(elementId)
  }

  async function unlink(elementId: string, referenceId: string): Promise<void> {
    unwrap(
      await api.DELETE('/external-references/{reference_id}', {
        params: { path: { reference_id: referenceId } },
      }),
    )
    await load(elementId)
  }

  /** A dry run unless told otherwise: the server returns what would be sent. */
  async function issue(
    elementId: string,
    fields: { target: string; scope: string; title: string; body: string },
    dryRun: boolean,
  ): Promise<ExternalIssue> {
    const answer = unwrap(
      await api.POST('/elements/{element_id}/external-issues', {
        params: { path: { element_id: elementId } },
        body: { ...fields, dry_run: dryRun },
      }),
    )
    if (!dryRun) {
      await load(elementId)
    }
    return answer
  }

  return { items, targets, status, error, load, link, refresh, refreshAll, unlink, issue }
}
