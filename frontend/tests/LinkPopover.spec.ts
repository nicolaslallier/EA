import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import LinkPopover from '../src/features/diagrams/LinkPopover.vue'
import { ApiError } from '../src/lib/api'
import { anElement, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const SOURCE = anElement({ id: 'aaaaaaaa-1111-4111-8111-111111111111', name: 'Facturation' })
const TARGET = anElement({
  id: 'bbbbbbbb-2222-4222-8222-222222222222',
  name: 'Commandes',
  element_type: 'business_process',
  layer: 'business',
})

function open(permitted: string[], connect = vi.fn(() => Promise.resolve())) {
  const calls = stubApi([{ path: '/metamodel/relationships', body: permitted }])
  const rendered = render(LinkPopover, { props: { source: SOURCE, target: TARGET, connect } })
  return { calls, connect, rendered }
}

describe('LinkPopover', () => {
  it('offers only what the metamodel permits for the pair, in that direction', async () => {
    const { calls } = open(['serving', 'association'])

    expect(await screen.findByRole('option', { name: 'sert' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'est associé à' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'compose' })).toBeNull()
    expect(calls[0].url.searchParams.get('source')).toBe('application_component')
    expect(calls[0].url.searchParams.get('target')).toBe('business_process')
  })

  it('creates the link with its name, and nothing it does not apply to', async () => {
    const { connect, rendered } = open(['serving'])
    await screen.findByRole('option', { name: 'sert' })

    await fireEvent.update(screen.getByLabelText(/type de relation/i), 'serving')
    await fireEvent.update(screen.getByLabelText(/intitulé/i), ' factures ')
    await fireEvent.click(screen.getByRole('button', { name: 'Relier' }))

    expect(connect).toHaveBeenCalledWith({
      relationship_type: 'serving',
      source_id: SOURCE.id,
      target_id: TARGET.id,
      name: 'factures',
    })
    await waitFor(() => expect(rendered.emitted().done).toHaveLength(1))
  })

  it('asks the access mode only for an access, and the direction only for an association', async () => {
    const { connect } = open(['access', 'association'])
    await screen.findByRole('option', { name: 'accède à' })
    expect(screen.queryByLabelText(/mode d'accès/i)).toBeNull()

    await fireEvent.update(screen.getByLabelText(/type de relation/i), 'association')
    expect(screen.queryByLabelText(/mode d'accès/i)).toBeNull()
    await fireEvent.click(screen.getByLabelText(/association orientée/i))
    await fireEvent.click(screen.getByRole('button', { name: 'Relier' }))

    expect(connect).toHaveBeenCalledWith(expect.objectContaining({ directed: true }))
    expect(connect).not.toHaveBeenCalledWith(expect.objectContaining({ access_type: expect.anything() }))

    await fireEvent.update(screen.getByLabelText(/type de relation/i), 'access')
    await fireEvent.update(screen.getByLabelText(/mode d'accès/i), 'read')
    await fireEvent.click(screen.getByRole('button', { name: 'Relier' }))

    expect(connect).toHaveBeenLastCalledWith(
      expect.not.objectContaining({ directed: expect.anything() }),
    )
    expect(connect).toHaveBeenLastCalledWith(expect.objectContaining({ access_type: 'read' }))
  })

  it("shows the API's refusal instead of closing", async () => {
    const connect = vi.fn(() =>
      Promise.reject(new ApiError('Relation refusée par le métamodèle.', 'invalid_input', 422)),
    )
    const { rendered } = open(['serving'], connect)
    await screen.findByRole('option', { name: 'sert' })

    await fireEvent.update(screen.getByLabelText(/type de relation/i), 'serving')
    await fireEvent.click(screen.getByRole('button', { name: 'Relier' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Relation refusée par le métamodèle.')
    expect(rendered.emitted().done).toBeUndefined()
  })

  it('says so when nothing may link the pair in that direction', async () => {
    open([])

    expect(await screen.findByText(/aucune relation n'est permise/i)).toBeInTheDocument()
  })
})
