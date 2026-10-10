import { fireEvent, render, screen, within } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useMetamodel } from '../src/features/metamodel/useMetamodel'
import { emptyQuery, type Query } from '../src/features/search/queryState'
import QueryBuilder from '../src/features/search/QueryBuilder.vue'
import { METAMODEL, aPackage, stubApi } from './support/api'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const FINANCE = aPackage({ id: 'f', name: 'Finance', path: 'Finance' })

async function show(query: Query = emptyQuery()) {
  stubApi([{ path: '/metamodel', body: METAMODEL }])
  const metamodel = useMetamodel()
  await metamodel.load()
  const view = render(QueryBuilder, {
    props: { modelValue: query, metamodel, packages: [FINANCE] },
  })
  const emitted = (): Record<string, unknown[][] | undefined> => view.emitted()
  const last = () => emitted()['update:modelValue']?.at(-1)?.[0] as Query
  return { emitted, last }
}

describe('QueryBuilder — what it offers comes from the metamodel', () => {
  it('lists the element types by layer and the layers, as /metamodel gave them', async () => {
    await show()

    const types = screen.getByLabelText(/types d'éléments/i)
    expect(Array.from(types.querySelectorAll('option')).map((o) => o.textContent?.trim())).toEqual([
      'Business Process',
      'Application Component',
    ])
    expect(screen.getByRole('checkbox', { name: 'Métier' })).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Application' })).toBeInTheDocument()
  })

  it('emits a new query when a type or a layer is chosen, leaving the original alone', async () => {
    const original = emptyQuery()
    const { last } = await show(original)

    const select = screen.getByLabelText(/types d'éléments/i)
    const option = within(select).getByRole('option', { name: 'Application Component' })
    ;(option as HTMLOptionElement).selected = true
    await fireEvent.change(select)
    expect(last().element_types).toEqual(['application_component'])

    await fireEvent.click(screen.getByRole('checkbox', { name: 'Métier' }))
    expect(last().layers).toEqual(['business'])
    expect(original.element_types).toEqual([])
  })
})

describe('QueryBuilder — the text', () => {
  it('waits for a pause in the typing before it asks, and a blank text is none', async () => {
    // Faked only once the palette has loaded: the stubbed responses need real timers.
    const { emitted, last } = await show()
    vi.useFakeTimers()

    await fireEvent.update(screen.getByLabelText(/contient/i), 'bill')
    await fireEvent.update(screen.getByLabelText(/contient/i), 'billing')
    expect(emitted()['update:modelValue']).toBeUndefined()

    vi.advanceTimersByTime(300)
    expect(last().text).toBe('billing')

    await fireEvent.update(screen.getByLabelText(/contient/i), '   ')
    vi.advanceTimersByTime(300)
    expect(last().text).toBeNull()
  })
})

describe('QueryBuilder — the package', () => {
  it('chooses a package, the unfiled, or everything', async () => {
    const { last } = await show()
    const choice = screen.getByLabelText(/^paquet$/i)

    await fireEvent.update(choice, 'f')
    expect(last()).toMatchObject({ package_id: 'f', root_only: false })

    await fireEvent.update(choice, 'root')
    expect(last()).toMatchObject({ package_id: null, root_only: true })

    await fireEvent.update(choice, '')
    expect(last()).toMatchObject({ package_id: null, root_only: false })
  })

  it('offers the subpackages checkbox only once a package is chosen', async () => {
    const { last } = await show({ ...emptyQuery(), package_id: 'f' })

    await fireEvent.click(screen.getByRole('checkbox', { name: /sous-paquets/i }))

    expect(last().include_subpackages).toBe(false)
  })

  it('hides it when no package is chosen', async () => {
    await show()

    expect(screen.queryByRole('checkbox', { name: /sous-paquets/i })).toBeNull()
  })
})

describe('QueryBuilder — the attribute tests', () => {
  it('adds a test, changes its operator, and drops the value an exists test cannot have', async () => {
    const { last } = await show()

    await fireEvent.click(screen.getByRole('button', { name: /ajouter un test de propriété/i }))
    expect(last().properties).toEqual([{ key: '', op: 'eq', value: '' }])

    await fireEvent.update(screen.getByLabelText(/nom de la propriété 1/i), 'owner')
    await fireEvent.update(screen.getByLabelText(/valeur de la propriété 1/i), 'finance')
    expect(last().properties).toEqual([{ key: 'owner', op: 'eq', value: 'finance' }])

    await fireEvent.update(screen.getByLabelText(/opérateur de la propriété 1/i), 'exists')
    expect(last().properties).toEqual([{ key: 'owner', op: 'exists', value: null }])
  })

  it('removes a test', async () => {
    const query: Query = {
      ...emptyQuery(),
      properties: [{ key: 'owner', op: 'exists', value: null }],
    }
    const { last } = await show(query)

    await fireEvent.click(screen.getByRole('button', { name: /retirer le test de propriété 1/i }))

    expect(last().properties).toEqual([])
  })
})

describe('QueryBuilder — the relation tests', () => {
  it('adds one with the strongest relationship of the metamodel, either way, present', async () => {
    const { last } = await show()

    await fireEvent.click(screen.getByRole('button', { name: /ajouter un test de relation/i }))

    expect(last().relations).toEqual([
      { relationship_type: 'composition', direction: 'any', present: true, other_type: null },
    ])
  })

  it('offers the relationships of /metamodel, in the verbs of the SPA, and the types for the far end', async () => {
    await show({
      ...emptyQuery(),
      relations: [
        { relationship_type: 'serving', direction: 'out', present: true, other_type: null },
      ],
    })

    const kind = screen.getByLabelText('Relation 1')
    expect(Array.from(kind.querySelectorAll('option')).map((o) => o.textContent?.trim())).toEqual([
      'compose',
      'sert',
    ])
    const far = screen.getByLabelText(/type de l'autre bout de la relation 1/i)
    expect(within(far).getAllByRole('option').map((o) => o.textContent?.trim())).toEqual([
      'Peu importe',
      'Business Process',
      'Application Component',
    ])
  })

  it('says has-no-such-link with the presence select, and narrows by far type', async () => {
    const query: Query = {
      ...emptyQuery(),
      relations: [
        { relationship_type: 'serving', direction: 'any', present: true, other_type: null },
      ],
    }
    const { last } = await show(query)

    await fireEvent.update(screen.getByLabelText(/présence de la relation 1/i), 'absent')
    expect(last().relations[0].present).toBe(false)

    await fireEvent.update(
      screen.getByLabelText(/type de l'autre bout de la relation 1/i),
      'business_process',
    )
    expect(last().relations[0].other_type).toBe('business_process')
  })
})

describe('QueryBuilder — submitting', () => {
  it('asks to search when the form is submitted', async () => {
    const { emitted } = await show()

    await fireEvent.submit(screen.getByRole('search', { name: /construire une recherche/i }))

    expect(emitted().submit).toHaveLength(1)
  })

  it('resets to the query that filters nothing', async () => {
    const { last } = await show({ ...emptyQuery(), text: 'db', layers: ['business'] })

    await fireEvent.click(screen.getByRole('button', { name: /réinitialiser/i }))

    expect(last()).toEqual(emptyQuery())
  })
})

describe('QueryBuilder — the keyboard', () => {
  it('puts the focus on the new row after adding a test, and on the add button after removing the last one', async () => {
    await show()

    await fireEvent.click(screen.getByRole('button', { name: /ajouter un test de propriété/i }))
    await vi.waitFor(() =>
      expect(screen.getByLabelText(/nom de la propriété 1/i)).toHaveFocus(),
    )

    await fireEvent.click(screen.getByRole('button', { name: /retirer le test de propriété 1/i }))
    await vi.waitFor(() =>
      expect(screen.getByRole('button', { name: /ajouter un test de propriété/i })).toHaveFocus(),
    )
  })

  it('does the same for a relation test', async () => {
    await show()

    await fireEvent.click(screen.getByRole('button', { name: /ajouter un test de relation/i }))
    await vi.waitFor(() => expect(screen.getByLabelText('Relation 1')).toHaveFocus())

    await fireEvent.click(screen.getByRole('button', { name: /retirer le test de relation 1/i }))
    await vi.waitFor(() =>
      expect(screen.getByRole('button', { name: /ajouter un test de relation/i })).toHaveFocus(),
    )
  })

  it('keeps the text being typed when the form is submitted before the pause is over', async () => {
    const { emitted, last } = await show()

    await fireEvent.update(screen.getByLabelText(/contient/i), 'billing')
    await fireEvent.submit(screen.getByRole('search', { name: /construire une recherche/i }))

    expect(last().text).toBe('billing')
    expect(emitted().submit).toHaveLength(1)
  })

  it('forgets a text still waiting for its pause when the form is reset', async () => {
    const { emitted, last } = await show()
    vi.useFakeTimers()

    await fireEvent.update(screen.getByLabelText(/contient/i), 'billing')
    await fireEvent.click(screen.getByRole('button', { name: /réinitialiser/i }))
    vi.advanceTimersByTime(500)

    expect(emitted()['update:modelValue']).toHaveLength(1)
    expect(last()).toEqual(emptyQuery())
  })
})
