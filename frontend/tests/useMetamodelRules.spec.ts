import { afterEach, describe, expect, it, vi } from 'vitest'

import { useMetamodelRules } from '../src/features/metamodel/useMetamodelRules'
import { aMatrix, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const ROW = aMatrix('application_component', {
  application_service: ['realization', 'serving', 'association'],
  business_process: ['serving', 'association'],
  business_object: ['association'],
})

describe('useMetamodelRules', () => {
  it('asks the backend for the row of the matrix rather than reimplementing it', async () => {
    const calls = stubApi([{ path: '/metamodel/matrix', body: ROW }])

    const rules = useMetamodelRules()
    await rules.load('application_component')

    expect(calls[0].url.pathname).toBe('/metamodel/matrix')
    expect(calls[0].url.searchParams.get('source')).toBe('application_component')
    expect(rules.status.value).toBe('ready')
    expect(rules.rules.value).toHaveLength(3)
  })

  it('answers which relationships one pair allows, and none for an unknown target', async () => {
    stubApi([{ path: '/metamodel/matrix', body: ROW }])

    const rules = useMetamodelRules()
    await rules.load('application_component')

    expect(rules.allowed('business_process')).toEqual(['serving', 'association'])
    expect(rules.allowed('junction')).toEqual([])
  })

  it('counts the targets a given relationship may point at', async () => {
    stubApi([{ path: '/metamodel/matrix', body: ROW }])

    const rules = useMetamodelRules()
    await rules.load('application_component')

    expect(rules.reach('serving')).toBe(2)
    expect(rules.reach('')).toBe(3)
  })

  it('reports a failure instead of showing rules that were never fetched', async () => {
    stubApi([{ path: '/metamodel/matrix', status: 422, body: { error: 'unknown', detail: 'Nope' } }])

    const rules = useMetamodelRules()
    await rules.load('application_component')

    expect(rules.status.value).toBe('error')
    expect(rules.rules.value).toEqual([])
    expect(rules.error.value).toBe('Nope')
  })
})
