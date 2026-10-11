import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import QualitySection from '../src/features/quality/QualitySection.vue'
import { createAppRouter } from '../src/router'
import { aFinding, aQualityReport, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

async function open(routes: Route[], query = '') {
  const calls = stubApi(routes)
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/analyse/qualite${query}`)
  await router.isReady()
  render(QualitySection, { global: { plugins: [router] } })
  return { calls, router }
}

const ORPHAN = aFinding()
const DUPLICATE = aFinding({
  rule: 'duplicate_probable',
  element_name: 'Facturation 2',
  message: '« Facturation 2 » ressemble à « Facturation ».',
})

describe('QualitySection', () => {
  it('counts each rule at the top, zeros included', async () => {
    await open([{ path: '/quality', body: aQualityReport([ORPHAN, DUPLICATE]) }])

    expect(await screen.findByRole('button', { name: /orphelin.*1/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /relation incohérente.*0/i })).toBeInTheDocument()
  })

  it('groups the findings by rule with a fix link each', async () => {
    await open([{ path: '/quality', body: aQualityReport([ORPHAN, DUPLICATE]) }])

    const table = await screen.findByRole('table')
    expect(within(table).getByText(/n'est relié à aucun élément/)).toBeInTheDocument()
    const links = within(table).getAllByRole('link', { name: /corriger/i })
    expect(links[0]).toHaveAttribute('href', ORPHAN.fix_hint)
  })

  it('shows an element name as text, never as markup', async () => {
    const hostile = aFinding({ element_name: '<img src=x onerror=alert(1)>', message: '<b>x</b>' })
    await open([{ path: '/quality', body: aQualityReport([hostile]) }])

    expect(await screen.findByText('<b>x</b>')).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
  })

  it('reads the filters from the URL and sends them', async () => {
    const { calls } = await open(
      [{ path: '/quality', body: aQualityReport([]) }],
      '?severity=error&code=orphan',
    )

    await screen.findByText(/aucun constat/i)
    expect(calls[0].url.searchParams.get('severity')).toBe('error')
    expect(calls[0].url.searchParams.get('code')).toBe('orphan')
  })

  it('writes a clicked rule into the URL, replacing the history entry', async () => {
    const { router } = await open([{ path: '/quality', body: aQualityReport([ORPHAN]) }])

    await fireEvent.click(await screen.findByRole('button', { name: /orphelin/i }))

    await waitFor(() => expect(router.currentRoute.value.query.code).toBe('orphan'))
  })

  it('warns that a rule was cut short, so a short list is not proof', async () => {
    await open([{ path: '/quality', body: aQualityReport([ORPHAN], { partial: true }) }])

    expect(await screen.findByRole('status')).toHaveTextContent(/doublons.*partiel/i)
  })

  it('says a clean model is clean', async () => {
    await open([{ path: '/quality', body: aQualityReport([]) }])

    expect(await screen.findByText(/aucun constat/i)).toBeInTheDocument()
  })

  it('offers the next page when the API gives a cursor', async () => {
    const { calls } = await open([
      { path: '/quality', body: aQualityReport([ORPHAN], { next_cursor: 1 }) },
    ])

    await fireEvent.click(await screen.findByRole('button', { name: /afficher la suite/i }))

    await waitFor(() => expect(calls[calls.length - 1].url.searchParams.get('cursor')).toBe('1'))
  })

  it('shows the refusal in words', async () => {
    await open([{ path: '/quality', status: 422, body: { error: 'scope_too_large', detail: 'catalogue trop large' } }])

    expect(await screen.findByRole('alert')).toHaveTextContent('catalogue trop large')
  })
})
