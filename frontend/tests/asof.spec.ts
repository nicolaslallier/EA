import { render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'
import { defineComponent, h } from 'vue'
import type { Router } from 'vue-router'
import { createMemoryHistory } from 'vue-router'

import { parseAsOf, useAsOf } from '../src/lib/asof'
import { createAppRouter } from '../src/router'

const allowed = { accessToken: () => Promise.resolve('token'), signIn: () => Promise.resolve() }

function mountUseAsOf(router: Router): ReturnType<typeof useAsOf> {
  let api: ReturnType<typeof useAsOf> | undefined
  render(
    defineComponent({
      setup() {
        api = useAsOf()
        return () => h('div')
      },
    }),
    { global: { plugins: [router] } },
  )
  if (!api) throw new Error('the composable did not run')
  return api
}

function aRouter() {
  return createAppRouter(createMemoryHistory(), allowed)
}

describe('parseAsOf', () => {
  it('keeps a real day and nothing else', () => {
    expect(parseAsOf('2028-01-01')).toBe('2028-01-01')
    expect(parseAsOf('2028-02-30')).toBeUndefined()
    expect(parseAsOf('2028-1-1')).toBeUndefined()
    expect(parseAsOf('demain')).toBeUndefined()
    expect(parseAsOf(['2028-01-01'])).toBeUndefined()
    expect(parseAsOf(undefined)).toBeUndefined()
    expect(parseAsOf('9'.repeat(5000))).toBeUndefined()
  })
})

describe('the asof query parameter', () => {
  it('follows the user from one section to the next', async () => {
    const router = aRouter()
    await router.push('/elements?asof=2028-01-01')

    await router.push('/relations')

    expect(router.currentRoute.value.path).toBe('/relations')
    expect(router.currentRoute.value.query.asof).toBe('2028-01-01')
  })

  it('does not invent a date nobody asked for', async () => {
    const router = aRouter()
    await router.push('/elements')

    await router.push('/relations')

    expect(router.currentRoute.value.query.asof).toBeUndefined()
  })

  it('lets a section ask for its own date', async () => {
    const router = aRouter()
    await router.push('/elements?asof=2028-01-01')

    await router.push('/relations?asof=2030-05-05')

    expect(router.currentRoute.value.query.asof).toBe('2030-05-05')
  })

  it('does not carry a date that is not a day', async () => {
    const router = aRouter()
    await router.push('/elements?asof=demain')

    await router.push('/relations')

    expect(router.currentRoute.value.query.asof).toBeUndefined()
  })

  it('does not bring the date back after it was cleared on the same page', async () => {
    const router = aRouter()
    await router.push('/elements?asof=2028-01-01')
    await router.isReady()
    const api = mountUseAsOf(router)

    api.setAsOf('')
    await new Promise((resolve) => setTimeout(resolve))

    expect(router.currentRoute.value.query.asof).toBeUndefined()
  })

  it('reads the day from the URL and writes it back with replace', async () => {
    const router = aRouter()
    await router.push('/elements?asof=2028-01-01&element=abc')
    await router.isReady()
    const api = mountUseAsOf(router)
    expect(api.asOf.value).toBe('2028-01-01')

    api.setAsOf('2030-02-02')
    await new Promise((resolve) => setTimeout(resolve))

    expect(router.currentRoute.value.query).toEqual({ asof: '2030-02-02', element: 'abc' })
  })
})
