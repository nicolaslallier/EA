import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Router } from 'vue-router'
import { createMemoryHistory } from 'vue-router'

import MetamodelSection from '../src/features/metamodel/MetamodelSection.vue'
import { createAppRouter } from '../src/router'
import { aMatrix, METAMODEL, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const PALETTE: Route = { path: '/metamodel', body: METAMODEL }
const MATRIX: Route = {
  path: '/metamodel/matrix',
  body: aMatrix('application_component', {
    application_component: ['composition', 'association'],
    business_process: ['serving', 'association'],
  }),
}

const ROUTES = [PALETTE, MATRIX]

async function open(query = ''): Promise<Router> {
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/metamodele${query}`)
  await router.isReady()
  render(MetamodelSection, { global: { plugins: [router] } })
  return router
}

describe('MetamodelSection', () => {
  it('lists the layers and the element types the backend publishes', async () => {
    stubApi(ROUTES)
    await open()

    expect(await screen.findByRole('heading', { name: /application/i })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /métier/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Application Component/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Business Process/ })).toBeInTheDocument()
  })

  it('explains each relationship by its family and the way impact travels', async () => {
    stubApi(ROUTES)
    await open()

    const row = (await screen.findByText('sert')).closest('tr')
    expect(row).not.toBeNull()
    expect(row).toHaveTextContent(/dépendance/i)
    expect(row).toHaveTextContent(/de la source vers la cible/i)

    const composition = screen.getByText('compose').closest('tr')
    expect(composition).toHaveTextContent(/structurelle/i)
    expect(composition).toHaveTextContent(/de la cible vers la source/i)
  })

  it('asks for no rule until a source type is chosen', async () => {
    const calls = stubApi(ROUTES)
    await open()

    await screen.findByRole('button', { name: /Application Component/ })
    expect(calls.some((call) => call.url.pathname === '/metamodel/matrix')).toBe(false)
    expect(screen.getByText(/choisis un type/i)).toBeInTheDocument()
  })

  it('reads the chosen type from the URL and shows what it may point at', async () => {
    const calls = stubApi(ROUTES)
    await open('?source=application_component')

    await waitFor(() =>
      expect(
        calls.find((call) => call.url.pathname === '/metamodel/matrix')?.url.searchParams.get(
          'source',
        ),
      ).toBe('application_component'),
    )

    const table = await screen.findByRole('table', { name: /règles/i })
    const row = within(table).getByText('Business Process').closest('tr')
    expect(row).toHaveTextContent('sert')
    expect(row).toHaveTextContent('est associé à')
  })

  it('puts the chosen type in the URL, so the question can be shared', async () => {
    stubApi(ROUTES)
    const router = await open()

    await fireEvent.click(await screen.findByRole('button', { name: /Application Component/ }))

    await waitFor(() =>
      expect(router.currentRoute.value.query.source).toBe('application_component'),
    )
  })

  it('narrows the rules to one relationship without leaving a history step', async () => {
    stubApi(ROUTES)
    const router = await open('?source=application_component')
    await screen.findByRole('table', { name: /règles/i })

    await fireEvent.update(screen.getByLabelText('Relation'), 'serving')

    await waitFor(() => expect(router.currentRoute.value.query.relation).toBe('serving'))
    const table = screen.getByRole('table', { name: /règles/i })
    expect(within(table).queryByText('Application Component')).toBeNull()
    expect(within(table).getByText('Business Process')).toBeInTheDocument()
  })

  it('walks the matrix by making a target the next source', async () => {
    stubApi(ROUTES)
    const router = await open('?source=application_component')
    const table = await screen.findByRole('table', { name: /règles/i })

    await fireEvent.click(within(table).getByRole('button', { name: /Business Process/ }))

    await waitFor(() => expect(router.currentRoute.value.query.source).toBe('business_process'))
  })

  it('reports a refused row instead of drawing an empty table', async () => {
    stubApi([
      PALETTE,
      { path: '/metamodel/matrix', status: 422, body: { error: 'unknown', detail: 'Type inconnu' } },
    ])
    await open('?source=application_component')

    expect(await screen.findByRole('alert')).toHaveTextContent('Type inconnu')
  })
})
