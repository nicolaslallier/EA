import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import ViewDiagram from '../src/features/view/ViewDiagram.vue'
import ViewElement from '../src/features/view/ViewElement.vue'
import ViewHome from '../src/features/view/ViewHome.vue'
import ViewPackage from '../src/features/view/ViewPackage.vue'
import { createAppRouter } from '../src/router'
import {
  aDocumentSummary,
  aGraph,
  anElement,
  aPackage,
  aPage,
  aRelationship,
  METAMODEL,
  stubApi,
  type Route,
} from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const A = anElement({ id: 'aaaaaaaa-1111-4111-8111-111111111111', name: 'Facturation' })
const B = anElement({
  id: 'bbbbbbbb-2222-4222-8222-222222222222',
  name: 'Commandes',
  element_type: 'business_process',
  layer: 'business',
})
const LINK = aRelationship({ source_id: A.id, target_id: B.id, relationship_type: 'serving' })
const DIAGRAM_ID = 'dddddddd-0000-4000-8000-000000000000'
const SUMMARY = {
  id: DIAGRAM_ID,
  name: 'Vue applicative',
  description: '',
  node_count: 2,
  created_at: '2026-09-13T12:00:00Z',
  updated_at: '2026-09-13T12:00:00Z',
}

const FINANCE = aPackage({ id: 'f1', name: 'Finance', path: 'Finance', parent_id: null })
const COMPTA = aPackage({ id: 'f2', name: 'Compta', path: 'Finance / Compta', parent_id: 'f1' })

const ROUTES: Route[] = [
  { path: '/packages', body: [FINANCE, COMPTA] },
  { path: '/metamodel', body: METAMODEL },
  { path: '/diagrams', body: [SUMMARY] },
  { path: '/elements', body: aPage([A, B]) },
  { path: `/elements/${A.id}`, body: A },
  { path: `/elements/${A.id}/relationships`, body: aGraph([A, B], [LINK]) },
  { path: `/elements/${A.id}/documents`, body: [aDocumentSummary({ element_id: A.id })] },
  { path: '/documents/dddddddd-1111-4111-8111-111111111111/html', body: '<h1>Runbook</h1>' },
  {
    path: `/diagrams/${DIAGRAM_ID}`,
    body: {
      ...SUMMARY,
      nodes: [
        { element_id: A.id, x: 10, y: 20, width: 132, height: 46 },
        { element_id: B.id, x: 300, y: 20, width: 132, height: 46 },
      ],
      elements: [A, B],
      relationships: [LINK],
    },
  },
  { path: `/diagrams/${DIAGRAM_ID}/render.svg`, body: '<svg></svg>' },
]

async function at(path: string, component: object) {
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  await router.isReady()
  const view = render(component, { global: { plugins: [router] } })
  return { router, view }
}

const WRITE_CONTROLS = /Supprimer|Modifier|Créer|Enregistrer|Déposer|Importer|Retirer/

describe('the reader mode never writes', () => {
  it('home: lists diagrams and finds elements by name, with GET only', async () => {
    const calls = stubApi(ROUTES)

    await at('/view?q=Fact', ViewHome)

    expect(await screen.findByRole('link', { name: 'Vue applicative' })).toHaveAttribute(
      'href',
      `/view/diagram/${DIAGRAM_ID}`,
    )
    expect(await screen.findByRole('link', { name: 'Facturation' })).toHaveAttribute(
      'href',
      `/view/element/${A.id}`,
    )
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
    expect(screen.queryByRole('button', { name: WRITE_CONTROLS })).toBeNull()
  })

  it('element: facts, clickable links both ways, documents rendered by the server', async () => {
    const calls = stubApi(ROUTES)

    await at(`/view/element/${A.id}`, ViewElement)

    expect(await screen.findByRole('heading', { name: 'Facturation' })).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'Commandes' })).toHaveAttribute(
      'href',
      `/view/element/${B.id}`,
    )
    await fireEvent.click(await screen.findByRole('button', { name: 'runbook.md' }))
    expect(await screen.findByRole('heading', { name: 'Runbook' })).toBeInTheDocument()
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
    expect(screen.queryByRole('button', { name: WRITE_CONTROLS })).toBeNull()
  })

  it('element: the relation filter is in the URL and asks the API for that type only', async () => {
    const calls = stubApi(ROUTES)
    const { router } = await at(`/view/element/${A.id}`, ViewElement)
    await screen.findByRole('link', { name: 'Commandes' })
    await screen.findByRole('option', { name: 'sert' })

    await fireEvent.update(screen.getByLabelText('Type de relation'), 'serving')

    await waitFor(() => expect(router.currentRoute.value.query.relation).toBe('serving'))
    await waitFor(() =>
      expect(
        calls.some(
          (call) =>
            call.url.pathname.endsWith('/relationships') &&
            call.url.searchParams.get('relationship_type') === 'serving',
        ),
      ).toBe(true),
    )
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('diagram: the server drawing as an image, then the elements under it, GET only', async () => {
    const createObjectURL = vi.fn(() => 'blob:drawing')
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }))
    const calls = stubApi(ROUTES)

    await at(`/view/diagram/${DIAGRAM_ID}`, ViewDiagram)

    const image = await screen.findByRole('img', { name: /Vue applicative/ })
    expect(image).toHaveAttribute('src', 'blob:drawing')
    expect(await screen.findByRole('link', { name: 'Commandes' })).toHaveAttribute(
      'href',
      `/view/element/${B.id}`,
    )
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
    // Download is offered, a deposit never is, whoever is logged in.
    expect(screen.queryByRole('button', { name: 'Déposer dans les fichiers' })).toBeNull()
    expect(screen.queryByRole('button', { name: WRITE_CONTROLS })).toBeNull()
  })

  it('diagram: an unknown diagram is said, not drawn', async () => {
    stubApi([
      {
        path: `/diagrams/${DIAGRAM_ID}`,
        status: 404,
        body: { error: 'not_found', detail: 'no diagram' },
      },
    ])

    await at(`/view/diagram/${DIAGRAM_ID}`, ViewDiagram)

    expect(await screen.findByRole('alert')).toHaveTextContent('no diagram')
    expect(screen.queryByRole('img')).toBeNull()
  })
})

describe('the reader mode reads packages', () => {
  it('home: lists the root packages, as links into the reader', async () => {
    const calls = stubApi(ROUTES)

    await at('/view', ViewHome)

    expect(await screen.findByRole('link', { name: 'Finance' })).toHaveAttribute(
      'href',
      '/view/package/f1',
    )
    expect(screen.queryByRole('link', { name: 'Compta' })).toBeNull()
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('package: its sub-packages, diagrams and own elements, GET only', async () => {
    const calls = stubApi(ROUTES)

    await at('/view/package/f1', ViewPackage)

    expect(await screen.findByRole('heading', { name: 'Finance' })).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'Compta' })).toHaveAttribute(
      'href',
      '/view/package/f2',
    )
    expect(await screen.findByRole('link', { name: 'Vue applicative' })).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'Facturation' })).toHaveAttribute(
      'href',
      `/view/element/${A.id}`,
    )
    const read = calls.filter((call) => call.url.pathname === '/elements')
    expect(read[0]?.url.searchParams.get('package')).toBe('f1')
    expect(read[0]?.url.searchParams.get('include_subpackages')).toBe('false')
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
    expect(screen.queryByRole('button', { name: WRITE_CONTROLS })).toBeNull()
  })

  it('package: offers a download of its report, never a deposit', async () => {
    stubApi(ROUTES)

    await at('/view/package/f1', ViewPackage)

    await fireEvent.click(await screen.findByRole('button', { name: /Générer un rapport/ }))
    expect(screen.queryByRole('button', { name: 'Déposer dans les fichiers' })).toBeNull()
  })

  it('package: an unknown package is said', async () => {
    stubApi(ROUTES)

    await at('/view/package/inconnu', ViewPackage)

    expect(await screen.findByRole('alert')).toHaveTextContent("n'existe pas")
  })
})

describe('the reader mode has no way to write, by construction', () => {
  it('none of its screens names a write verb of the API client', () => {
    const sources = import.meta.glob('../src/features/view/*.vue', {
      query: '?raw',
      import: 'default',
      eager: true,
    })

    expect(Object.keys(sources).length).toBeGreaterThanOrEqual(5)
    for (const [file, source] of Object.entries(sources)) {
      expect(source, file).not.toMatch(/api\.(POST|PUT|PATCH|DELETE)\b/)
      expect(source, file).not.toMatch(/api\[\s*['"`](POST|PUT|PATCH|DELETE)/)
      expect(source, file).not.toMatch(/\bfetch\(/)
      expect(source, file).not.toMatch(/method:\s*['"`](POST|PUT|PATCH|DELETE)/)
    }
  })
})
