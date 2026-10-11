import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'
import type { Component } from 'vue'
import { createMemoryHistory } from 'vue-router'

import App from '../src/App.vue'
import AsOfPicker from '../src/components/AsOfPicker.vue'
import { createAppRouter } from '../src/router'

async function open(path: string, component: Component = AsOfPicker) {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input)
      const body = url.endsWith('/health') ? { status: 'ok' } : []
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }))
    }),
  )
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  await router.isReady()
  render(component, { global: { plugins: [router] } })
  return router
}

describe('AsOfPicker', () => {
  it('shows no date by default: today, no filter', async () => {
    await open('/elements')

    expect(screen.getByLabelText('Vue au')).toHaveValue('')
    expect(screen.queryByRole('button', { name: /aujourd/i })).toBeNull()
  })

  it('writes the chosen day into the URL and keeps the rest of it', async () => {
    const router = await open('/elements?element=abc')

    await fireEvent.update(screen.getByLabelText('Vue au'), '2028-01-01')

    await waitFor(() => expect(router.currentRoute.value.query.asof).toBe('2028-01-01'))
    expect(router.currentRoute.value.query.element).toBe('abc')
  })

  it('reads the day from the URL and clears it on request', async () => {
    const router = await open('/elements?asof=2028-01-01')
    expect(screen.getByLabelText('Vue au')).toHaveValue('2028-01-01')

    await fireEvent.click(screen.getByRole('button', { name: /aujourd/i }))

    await waitFor(() => expect(router.currentRoute.value.query.asof).toBeUndefined())
  })
})

describe('the shell banner', () => {
  it('reminds that the catalogue is filtered while a day is chosen', async () => {
    await open('/nulle-part?asof=2028-01-01', App)

    expect(
      await screen.findByText('Vue au 2028-01-01 — le catalogue est filtré à cette date.'),
    ).toBeInTheDocument()
  })

  it('says nothing when no day is chosen', async () => {
    await open('/nulle-part', App)

    expect(screen.queryByText(/le catalogue est filtré/i)).toBeNull()
  })
})
