import { afterEach, describe, expect, it, vi } from 'vitest'

import { createLogger, resolveLevel } from '../src/lib/logging'

afterEach(() => {
  vi.restoreAllMocks()
})

// The SPA is loaded from another machine as readily as from localhost
// (docs/adr/0019), so "check the terminal" is not an answer when a screen stays
// empty: the only trace of what the browser asked for is in the browser. See
// docs/adr/0021.
describe('resolveLevel', () => {
  it('says everything while developing', () => {
    expect(resolveLevel(undefined, true)).toBe('debug')
  })

  it('keeps a built bundle to what went wrong', () => {
    expect(resolveLevel(undefined, false)).toBe('warn')
  })

  it('takes the level it is given', () => {
    expect(resolveLevel('error', true)).toBe('error')
    expect(resolveLevel('SILENT', false)).toBe('silent')
  })

  it('falls back rather than throwing on a level nobody defined', () => {
    expect(resolveLevel('chatty', true)).toBe('debug')
  })
})

describe('a logger', () => {
  it('says who is speaking', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})

    createLogger('api', 'debug').warn('backend injoignable')

    expect(warn).toHaveBeenCalledWith('[api] backend injoignable')
  })

  it('passes the fields on as an object, not as prose', () => {
    const debug = vi.spyOn(console, 'debug').mockImplementation(() => {})

    createLogger('api', 'debug').debug('GET /elements', { status: 200 })

    expect(debug).toHaveBeenCalledWith('[api] GET /elements', { status: 200 })
  })

  it('drops what sits below the level', () => {
    const debug = vi.spyOn(console, 'debug').mockImplementation(() => {})
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})

    const logger = createLogger('api', 'warn')
    logger.debug('noise')
    logger.error('boom')

    expect(debug).not.toHaveBeenCalled()
    expect(error).toHaveBeenCalled()
  })

  it('says nothing at all when silenced', () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})

    createLogger('api', 'silent').error('boom')

    expect(error).not.toHaveBeenCalled()
  })
})
