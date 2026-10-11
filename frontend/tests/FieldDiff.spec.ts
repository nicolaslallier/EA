import { fireEvent, render, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import FieldDiff from '../src/features/history/FieldDiff.vue'

describe('FieldDiff', () => {
  it('shows each field with what it was and what it became', () => {
    render(FieldDiff, {
      props: {
        changes: [
          { field: 'name', before: 'db-01', after: 'db-02' },
          { field: 'properties.owner', before: null, after: 'ops' },
        ],
      },
    })

    expect(screen.getByText('name')).toBeTruthy()
    expect(screen.getByText('db-01')).toBeTruthy()
    expect(screen.getByText('db-02')).toBeTruthy()
    expect(screen.getByText('properties.owner')).toBeTruthy()
    expect(screen.getByText('—')).toBeTruthy() // an absent value is a dash, not "null"
  })

  it('says so when nothing differs', () => {
    render(FieldDiff, { props: { changes: [] } })

    expect(screen.getByText('Aucun champ modifié.')).toBeTruthy()
  })

  it('shows a value as text, never as markup', () => {
    const { container } = render(FieldDiff, {
      props: { changes: [{ field: 'name', before: '<img src=x onerror=alert(1)>', after: 'ok' }] },
    })

    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeTruthy()
  })

  it('shows an object as JSON', () => {
    render(FieldDiff, { props: { changes: [{ field: 'query', before: null, after: { a: 1 } }] } })

    expect(screen.getByText('{"a":1}')).toBeTruthy()
  })

  it('cuts a huge value, says so, and gives the whole of it on request', async () => {
    const huge = 'x'.repeat(5000)
    render(FieldDiff, { props: { changes: [{ field: 'documentation', before: null, after: huge }] } })

    expect(screen.queryByText(huge)).toBeNull()
    expect(screen.getByText(/tronqué.*5000 caractères/)).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: 'Tout afficher' }))
    expect(screen.getByText(huge)).toBeTruthy()
  })
})
