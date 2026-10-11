import { fireEvent, render, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import RoadmapChart from '../src/components/RoadmapChart.vue'
import { aRoadmapItem } from './support/api'

const WINDOW = { from: '2026-01-01', to: '2026-12-31' }
const PHASE = aRoadmapItem({
  id: 'p1',
  name: 'Phase 1',
  valid_from: '2026-02-01',
  valid_until: '2026-05-31',
})
const TO_BE = aRoadmapItem({
  id: 'p2',
  name: 'To-Be',
  element_type: 'plateau',
  valid_from: '2026-09-01',
})
const RUNBOOK = aRoadmapItem({ id: 'p3', name: 'Runbook', element_type: 'deliverable' })

function draw(props: Record<string, unknown> = {}) {
  return render(RoadmapChart, {
    props: { items: [PHASE, TO_BE, RUNBOOK], window: WINDOW, ...props },
  })
}

describe('RoadmapChart', () => {
  it('draws a bar per dated work package and a diamond per plateau, each a focusable control', () => {
    draw()

    const bar = screen.getByRole('button', { name: 'Phase 1 — du 2026-02-01 au 2026-05-31' })
    const milestone = screen.getByRole('button', { name: 'To-Be — du 2026-09-01 au …' })
    expect(bar).toHaveAttribute('tabindex', '0')
    expect(milestone).toHaveAttribute('tabindex', '0')
    expect(document.querySelector('polygon')).not.toBeNull()
  })

  it('opens the detail of an element on click and on Enter', async () => {
    const rendered = draw()

    await fireEvent.click(screen.getByRole('button', { name: /Phase 1/ }))
    await fireEvent.keyDown(screen.getByRole('button', { name: /To-Be/ }), { key: 'Enter' })

    expect(rendered.emitted().open).toEqual([['p1'], ['p2']])
  })

  it('lists an undated element in its own band instead of hiding it', async () => {
    const rendered = draw()

    const band = screen.getByRole('region', { name: /non daté/i })
    await fireEvent.click(screen.getByRole('button', { name: 'Runbook' }))

    expect(band).toHaveTextContent('Runbook')
    expect(rendered.emitted().open).toEqual([['p3']])
  })

  it('draws the as-of day as a vertical line', () => {
    draw({ asOf: '2026-06-01' })

    expect(screen.getByTestId('asof-line')).toBeInTheDocument()
  })

  it('draws no line when no day is asked', () => {
    draw()

    expect(screen.queryByTestId('asof-line')).toBeNull()
  })
})
