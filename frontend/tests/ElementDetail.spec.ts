import { fireEvent, render, screen, within } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import ElementDetail from '../src/features/elements/ElementDetail.vue'
import { createAppRouter } from '../src/router'
import { anElement } from './support/api'

async function show(element = anElement(), typeLabel = 'Application Component') {
  const router = createAppRouter(createMemoryHistory())
  await router.push('/elements')
  await router.isReady()
  return render(ElementDetail, {
    props: { element, typeLabel },
    global: { plugins: [router] },
  })
}

describe('ElementDetail', () => {
  it('shows what the row could not: the whole element', async () => {
    await show(
      anElement({
        name: 'Facturation',
        description: 'Émet les factures',
        documentation: 'Reprise du mainframe en 2024.',
      }),
    )

    const panel = within(screen.getByRole('region', { name: /détail de l'élément/i }))
    expect(panel.getByRole('heading', { name: /Facturation/ })).toBeInTheDocument()
    expect(panel.getByText('Application Component')).toBeInTheDocument()
    expect(panel.getByText('Application')).toBeInTheDocument()
    expect(panel.getByText('Structure active')).toBeInTheDocument()
    expect(panel.getByText('Émet les factures')).toBeInTheDocument()
    expect(panel.getByText(/mainframe/)).toBeInTheDocument()
  })

  it('lists the attributes the user added to the element', async () => {
    await show(anElement({ properties: { criticité: 'haute', propriétaire: 'DSI' } }))

    const panel = within(screen.getByRole('region', { name: /détail de l'élément/i }))
    expect(panel.getByText('criticité')).toBeInTheDocument()
    expect(panel.getByText('haute')).toBeInTheDocument()
    expect(panel.getByText('propriétaire')).toBeInTheDocument()
  })

  it('says an empty field is empty rather than showing a blank', async () => {
    await show(anElement({ description: '', documentation: '', properties: {} }))

    const panel = within(screen.getByRole('region', { name: /détail de l'élément/i }))
    expect(panel.getAllByText(/non renseigné/i).length).toBeGreaterThan(0)
    expect(panel.getByText(/aucun attribut/i)).toBeInTheDocument()
  })

  it('offers to look around the element without leaving the id behind', async () => {
    const element = anElement()
    await show(element)

    const link = screen.getByRole('link', { name: /voisinage/i })
    expect(link).toHaveAttribute('href', `/analyse/voisinage?element=${element.id}`)
  })

  it('closes on demand', async () => {
    const { emitted } = await show()

    await fireEvent.click(screen.getByRole('button', { name: /fermer/i }))

    expect(emitted().close).toBeTruthy()
  })
})
