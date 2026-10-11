import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'
import type { Router } from 'vue-router'
import { createMemoryHistory } from 'vue-router'

import DiagramsSection from '../src/features/diagrams/DiagramsSection.vue'
import { useMe } from '../src/lib/me'
import { createAppRouter } from '../src/router'
import { aPackage, anElement, aPage, METAMODEL, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const DIAGRAM_ID = 'dddddddd-0000-4000-8000-000000000000'
const A = anElement({ id: 'aaaaaaaa-1111-4111-8111-111111111111', name: 'Facturation' })
const B = anElement({
  id: 'bbbbbbbb-2222-4222-8222-222222222222',
  name: 'Commandes',
  element_type: 'business_process',
  layer: 'business',
})

const SUMMARY = {
  id: DIAGRAM_ID,
  name: 'Vue applicative',
  description: '',
  node_count: 1,
  package_id: null,
  created_at: '2026-09-13T12:00:00Z',
  updated_at: '2026-09-13T12:00:00Z',
}

const LIST: Route = { path: '/diagrams', body: [SUMMARY] }
const READ: Route = {
  path: `/diagrams/${DIAGRAM_ID}`,
  body: {
    ...SUMMARY,
    nodes: [{ element_id: A.id, x: 10, y: 20, width: 132, height: 46, parent_object_id: null, z: 0 }],
    elements: [A],
    relationships: [],
    revision: 0,
    objects: [],
    edges: [],
  },
}
const PALETTE: Route = { path: '/elements', body: aPage([A, B]) }
const METAMODEL_ROUTE: Route = { path: '/metamodel', body: METAMODEL }
const DETAIL: Route = { path: `/elements/${A.id}`, body: A }
const SAVE: Route = { method: 'PUT', path: `/diagrams/${DIAGRAM_ID}/layout`, status: 204 }

const FINANCE = aPackage({ id: 'f', name: 'Finance', path: 'Finance' })
const PACKAGES: Route = { path: '/packages', body: [FINANCE] }

const ROUTES = [LIST, READ, PALETTE, METAMODEL_ROUTE, DETAIL, SAVE, PACKAGES]

async function open(query = ''): Promise<Router> {
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/diagrammes${query}`)
  await router.isReady()
  render(DiagramsSection, { global: { plugins: [router] } })
  return router
}

describe('DiagramsSection — the list', () => {
  it('lists the saved diagrams when none is open', async () => {
    stubApi(ROUTES)
    await open()

    expect(await screen.findByText('Vue applicative')).toBeInTheDocument()
    expect(screen.getByText(/1 élément/)).toBeInTheDocument()
  })

  it('creates a diagram by name and opens it', async () => {
    const calls = stubApi([
      { method: 'POST', path: '/diagrams', status: 201, body: SUMMARY },
      ...ROUTES,
    ])
    const router = await open()
    await screen.findByText('Vue applicative')

    await fireEvent.update(screen.getByLabelText(/nom du diagramme/i), '  Vue applicative ')
    await fireEvent.click(screen.getByRole('button', { name: 'Créer' }))

    await waitFor(() => expect(router.currentRoute.value.query.diagram).toBe(DIAGRAM_ID))
    expect(calls.find((call) => call.method === 'POST')?.body).toEqual({ name: 'Vue applicative' })
  })

  it('shows why a diagram could not be created', async () => {
    stubApi([
      {
        method: 'POST',
        path: '/diagrams',
        status: 409,
        body: { error: 'duplicate', detail: 'Ce nom est déjà pris.' },
      },
      ...ROUTES,
    ])
    await open()
    await screen.findByText('Vue applicative')

    await fireEvent.update(screen.getByLabelText(/nom du diagramme/i), 'Vue applicative')
    await fireEvent.click(screen.getByRole('button', { name: 'Créer' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Ce nom est déjà pris.')
  })

  it('deletes a diagram only once the deletion is confirmed', async () => {
    const calls = stubApi([
      { method: 'DELETE', path: `/diagrams/${DIAGRAM_ID}`, status: 204 },
      ...ROUTES,
    ])
    await open()
    await screen.findByText('Vue applicative')

    await fireEvent.click(screen.getByRole('button', { name: 'Supprimer' }))
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    const confirmation = screen.getByRole('alert')
    expect(confirmation).toHaveTextContent(/les éléments sont conservés/i)

    await fireEvent.click(within(confirmation).getByRole('button', { name: 'Confirmer' }))

    await waitFor(() => expect(calls.some((call) => call.method === 'DELETE')).toBe(true))
  })
})

describe('DiagramsSection — the editor', () => {
  it('opens the diagram named in the URL, with every element in the palette by layer', async () => {
    stubApi(ROUTES)
    await open(`?diagram=${DIAGRAM_ID}`)

    expect(await screen.findByRole('heading', { name: 'Vue applicative' })).toBeInTheDocument()
    const palette = screen.getByRole('region', { name: /palette/i })
    expect(await within(palette).findByRole('heading', { name: 'Métier' })).toBeInTheDocument()
    expect(within(palette).getByRole('heading', { name: 'Application' })).toBeInTheDocument()
  })

  it('offers an element to drag only while it is not on the diagram yet', async () => {
    stubApi(ROUTES)
    await open(`?diagram=${DIAGRAM_ID}`)
    const palette = await screen.findByRole('region', { name: /palette/i })

    const free = await within(palette).findByText('Commandes')
    const placed = within(palette).getByText('Facturation')

    expect(free.closest('li')).toHaveAttribute('draggable', 'true')
    expect(placed.closest('li')).toHaveAttribute('draggable', 'false')
  })

  it('details the selected element, keeping the selection in the URL', async () => {
    stubApi(ROUTES)
    const router = await open(`?diagram=${DIAGRAM_ID}`)
    const canvas = await screen.findByRole('group', { name: /zone de dessin/i })
    const box = within(canvas).getByRole('button', { name: /Facturation/ })

    await fireEvent.click(box)

    await waitFor(() => expect(router.currentRoute.value.query.element).toBe(A.id))
    const detail = await screen.findByRole('region', { name: /détail de l'élément/i })
    expect(within(detail).getByRole('heading', { name: 'Facturation' })).toBeInTheDocument()
  })

  it('takes the selected element off the diagram without deleting it', async () => {
    const calls = stubApi(ROUTES)
    const router = await open(`?diagram=${DIAGRAM_ID}&element=${A.id}`)

    await fireEvent.click(await screen.findByRole('button', { name: 'Retirer du diagramme' }))

    await waitFor(() => expect(router.currentRoute.value.query.element).toBeUndefined())
    const canvas = screen.getByRole('group', { name: /zone de dessin/i })
    expect(within(canvas).queryByRole('button', { name: /Facturation/ })).toBeNull()
    // The layout is saved without the box, and the element itself is never deleted.
    await waitFor(() => expect(calls.some((call) => call.method === 'PUT')).toBe(true), {
      timeout: 2000,
    })
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
  })

  it('reports a diagram that cannot be read', async () => {
    stubApi([
      { ...READ, status: 404, body: { error: 'not_found', detail: 'Diagramme introuvable.' } },
      LIST,
      PALETTE,
      METAMODEL_ROUTE,
    ])
    await open(`?diagram=${DIAGRAM_ID}`)

    expect(await screen.findByText('Diagramme introuvable.')).toBeInTheDocument()
  })
})

describe('DiagramsSection — annotations', () => {
  it('adds a note from the annotation palette and saves it', async () => {
    const calls = stubApi(ROUTES)
    await open(`?diagram=${DIAGRAM_ID}`)

    await fireEvent.click(await screen.findByRole('button', { name: 'Ajouter une note' }))

    expect(await screen.findByLabelText('Texte de la note')).toBeInTheDocument()
    await waitFor(
      () => {
        const put = calls.find((call) => call.method === 'PUT')
        expect((put?.body as { objects: unknown[] }).objects).toHaveLength(1)
      },
      { timeout: 2000 },
    )
  })

  it('offers to reload when a colleague saved first, and pastes my work back on request', async () => {
    const calls = stubApi([
      { method: 'PUT', path: `/diagrams/${DIAGRAM_ID}/layout`, status: 409,
        body: { error: 'diagram_revision_conflict', detail: 'the diagram is at revision 5, not 0' } },
      LIST, READ, PALETTE, METAMODEL_ROUTE, DETAIL,
    ])
    await open(`?diagram=${DIAGRAM_ID}`)
    await fireEvent.click(await screen.findByRole('button', { name: 'Ajouter une note' }))

    const banner = (
      await screen.findByText(/quelqu'un a enregistré/i, {}, { timeout: 2000 })
    ).closest('p') as HTMLElement
    await fireEvent.click(within(banner).getByRole('button', { name: 'Recharger sa version' }))
    await waitFor(() => expect(calls.filter((call) => call.url.pathname === `/diagrams/${DIAGRAM_ID}`).length).toBeGreaterThan(1))
    await fireEvent.click(await screen.findByRole('button', { name: 'Recoller mes modifications' }))

    expect(screen.queryByText(/quelqu'un a enregistré/i)).toBeNull()
  })
})

describe('DiagramsSection — the toolbar', () => {
  it('undoes a removal from the toolbar and saves the restored layout', async () => {
    const calls = stubApi(ROUTES)
    await open(`?diagram=${DIAGRAM_ID}&element=${A.id}`)
    await fireEvent.click(await screen.findByRole('button', { name: 'Retirer du diagramme' }))
    const canvas = screen.getByRole('group', { name: /zone de dessin/i })
    expect(within(canvas).queryByRole('button', { name: /Facturation/ })).toBeNull()

    await fireEvent.click(screen.getByRole('button', { name: 'Annuler' }))

    expect(await within(canvas).findByRole('button', { name: /Facturation/ })).toBeInTheDocument()
    // The removal and its undo fall in one pause: a single save, of the restored layout.
    await waitFor(() => expect(calls.filter((call) => call.method === 'PUT')).toHaveLength(1), {
      timeout: 3000,
    })
    const saved = calls.find((call) => call.method === 'PUT')?.body as {
      nodes: { element_id: string }[]
    }
    expect(saved.nodes.map((node) => node.element_id)).toEqual([A.id])
  })

  it('undoes with Ctrl+Z', async () => {
    stubApi(ROUTES)
    await open(`?diagram=${DIAGRAM_ID}&element=${A.id}`)
    await fireEvent.click(await screen.findByRole('button', { name: 'Retirer du diagramme' }))

    await fireEvent.keyDown(window, { key: 'z', ctrlKey: true })

    expect(
      await within(screen.getByRole('group', { name: /zone de dessin/i })).findByRole('button', {
        name: /Facturation/,
      }),
    ).toBeInTheDocument()
  })
})

describe('DiagramsSection — a reader', () => {
  function asAReader(): void {
    vi.mocked(useMe).mockReturnValueOnce({
      me: ref({ username: 'reader', can_write: false, can_approve: false }),
      canWrite: computed(() => false),
      error: ref(null),
      load: vi.fn(() => Promise.resolve()),
    })
  }

  it('lists and opens the diagrams, but is offered no way to create or delete one', async () => {
    stubApi(ROUTES)
    asAReader()
    await open()

    expect(await screen.findByRole('link', { name: 'Vue applicative' })).toBeInTheDocument()
    expect(screen.queryByLabelText(/nom du diagramme/i)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Créer' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Supprimer' })).toBeNull()
  })

  it('sees a diagram without the palette, the link handle or a way to take a box off', async () => {
    stubApi(ROUTES)
    asAReader()
    await open(`?diagram=${DIAGRAM_ID}&element=${A.id}`)

    const canvas = await screen.findByRole('group', { name: /zone de dessin/i })
    expect(within(canvas).getByRole('button', { name: /Facturation/ })).toBeInTheDocument()
    await screen.findByRole('region', { name: /détail de l'élément/i })
    expect(screen.queryByRole('region', { name: /palette/i })).toBeNull()
    expect(screen.queryByLabelText(/relier « Facturation »/i)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Retirer du diagramme' })).toBeNull()
    expect(screen.queryByText('Enregistré automatiquement.')).toBeNull()
  })

  it('shows no annotation palette and no inspector controls', async () => {
    stubApi(ROUTES)
    asAReader()
    await open(`?diagram=${DIAGRAM_ID}`)
    await screen.findByRole('group', { name: /zone de dessin/i })

    expect(screen.queryByRole('button', { name: 'Ajouter une note' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Ajouter un groupe' })).toBeNull()
  })

  it('hides the whole editing toolbar, and Ctrl+Z does nothing', async () => {
    stubApi(ROUTES)
    asAReader()
    await open(`?diagram=${DIAGRAM_ID}`)
    await screen.findByRole('group', { name: /zone de dessin/i })

    expect(screen.queryByRole('button', { name: 'Annuler' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Exporter en SVG' })).toBeInTheDocument()
    await fireEvent.keyDown(window, { key: 'z', ctrlKey: true })
    expect(screen.getByRole('button', { name: /Facturation/ })).toBeInTheDocument()
  })
})

describe('DiagramsSection — the package tree', () => {
  it('filters the list by the package the URL names', async () => {
    const calls = stubApi(ROUTES)
    await open('?package=f&subpackages=0')

    await screen.findByText('Vue applicative')
    const query = calls.find((call) => call.url.pathname === '/diagrams')?.url.searchParams
    expect(query?.get('package')).toBe('f')
    expect(query?.get('include_subpackages')).toBe('false')
  })

  it('files a new diagram in the chosen package, and sends nothing when none is chosen', async () => {
    const calls = stubApi([
      { method: 'POST', path: '/diagrams', status: 201, body: SUMMARY },
      ...ROUTES,
    ])
    await open()
    await screen.findByText('Vue applicative')

    await fireEvent.update(screen.getByLabelText(/nom du diagramme/i), 'Compta')
    await fireEvent.update(screen.getByLabelText(/^paquet$/i), 'f')
    await fireEvent.click(screen.getByRole('button', { name: 'Créer' }))

    await waitFor(() =>
      expect(calls.find((call) => call.method === 'POST')?.body).toEqual({
        name: 'Compta',
        package_id: 'f',
      }),
    )
  })

  it('moves a diagram to another package from its row, the root included', async () => {
    const calls = stubApi([
      { method: 'PATCH', path: `/diagrams/${DIAGRAM_ID}`, body: SUMMARY },
      ...ROUTES,
    ])
    await open()
    await screen.findByText('Vue applicative')

    await fireEvent.update(screen.getByLabelText(/ranger vue applicative dans/i), 'root')

    await waitFor(() =>
      expect(calls.find((call) => call.method === 'PATCH')?.body).toEqual({ package_id: null }),
    )
  })

  it('lets a reader read the tree and filter by it, but not file anything', async () => {
    vi.mocked(useMe).mockReturnValueOnce({
      me: ref({ username: 'reader', can_write: false, can_approve: false }),
      canWrite: computed(() => false),
      error: ref(null),
      load: vi.fn(() => Promise.resolve()),
    })
    stubApi(ROUTES)
    await open()

    expect(await screen.findByRole('treeitem', { name: /Finance/ })).toBeInTheDocument()
    expect(screen.queryByLabelText(/ranger vue applicative dans/i)).toBeNull()
    expect(screen.queryByLabelText(/^paquet$/i)).toBeNull()
  })

  it('puts the select back on the real package when the move is refused', async () => {
    stubApi([
      {
        method: 'PATCH',
        path: `/diagrams/${DIAGRAM_ID}`,
        status: 404,
        body: { error: 'not_found', detail: 'Paquet introuvable.' },
      },
      ...ROUTES,
    ])
    await open()
    await screen.findByText('Vue applicative')
    const select = screen.getByLabelText(/ranger vue applicative dans/i)

    await fireEvent.update(select, 'f')

    expect(await screen.findByRole('alert')).toHaveTextContent('Paquet introuvable.')
    expect(select).toHaveValue('root')
  })
})
