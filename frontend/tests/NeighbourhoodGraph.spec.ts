import { fireEvent, render, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import NeighbourhoodGraph from '../src/features/neighbourhood/NeighbourhoodGraph.vue'
import { aGraph, anElement, aRelationship } from './support/api'

const SUBJECT = anElement({ id: 'aaaaaaaa-1111-4111-8111-111111111111', name: 'Facturation' })
const NEIGHBOUR = anElement({
  id: 'bbbbbbbb-2222-4222-8222-222222222222',
  name: 'Commandes',
  element_type: 'business_process',
  layer: 'business',
})

const GRAPH = aGraph(
  [SUBJECT, NEIGHBOUR],
  [aRelationship({ source_id: SUBJECT.id, target_id: NEIGHBOUR.id })],
)

function draw(graph = GRAPH) {
  return render(NeighbourhoodGraph, {
    props: { graph, rootId: SUBJECT.id, typeLabel: () => 'Processus métier' },
  })
}

describe('NeighbourhoodGraph', () => {
  it('draws one box per element of the sub-graph', () => {
    draw()

    expect(screen.getByText(SUBJECT.name)).toBeInTheDocument()
    expect(screen.getByText(NEIGHBOUR.name)).toBeInTheDocument()
  })

  it('writes each relationship as its verb rather than its code', () => {
    draw()

    expect(screen.getByText('sert')).toBeInTheDocument()
  })

  it('tells the subject apart from the elements around it', () => {
    draw()

    expect(screen.getByLabelText(`Sujet : ${SUBJECT.name}`)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: `Centrer sur ${SUBJECT.name}` })).toBeNull()
  })

  it('offers every neighbour as the next centre', async () => {
    const { emitted } = draw()

    await fireEvent.click(screen.getByRole('button', { name: `Centrer sur ${NEIGHBOUR.name}` }))

    expect(emitted().focus).toEqual([[NEIGHBOUR]])
  })

  it('recentres from the keyboard, not only from a click', async () => {
    const { emitted } = draw()

    await fireEvent.keyDown(screen.getByRole('button', { name: /Centrer sur/ }), { key: 'Enter' })

    expect(emitted().focus).toEqual([[NEIGHBOUR]])
  })

  it('counts what it drew, for whoever cannot see the drawing', () => {
    draw()

    expect(screen.getByText(/2 éléments, 1 relation, jusqu'à 1 saut\./)).toBeInTheDocument()
  })

  it('legends only the layers it actually drew', () => {
    draw()

    expect(screen.getByText('Application')).toBeInTheDocument()
    expect(screen.getByText('Métier')).toBeInTheDocument()
    expect(screen.queryByText('Technologie')).toBeNull()
  })

  it('draws the element alone when the traversal found no neighbour', () => {
    draw(aGraph([SUBJECT], []))

    expect(screen.getByLabelText(`Sujet : ${SUBJECT.name}`)).toBeInTheDocument()
    expect(screen.queryAllByRole('button', { name: /Centrer sur/ })).toHaveLength(0)
  })
})
