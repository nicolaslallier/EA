import { fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ThemeToggle from '../src/components/ThemeToggle.vue'
import { applyStoredTheme, storedTheme, THEME_KEY } from '../src/lib/theme'

beforeEach(() => {
  localStorage.clear()
  delete document.documentElement.dataset.theme
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('the theme', () => {
  it('is light unless a dark one was chosen in this browser', () => {
    expect(storedTheme()).toBe('light')

    localStorage.setItem(THEME_KEY, 'dark')
    expect(storedTheme()).toBe('dark')
  })

  it('ignores a stored value it does not know', () => {
    localStorage.setItem(THEME_KEY, 'sepia')

    expect(storedTheme()).toBe('light')
  })

  it('applies the stored choice to the document', () => {
    localStorage.setItem(THEME_KEY, 'dark')

    applyStoredTheme()

    expect(document.documentElement.dataset.theme).toBe('dark')
  })

  it('falls back to light when the storage throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })

    expect(storedTheme()).toBe('light')
    expect(() => applyStoredTheme()).not.toThrow()
    expect(document.documentElement.dataset.theme).toBe('light')
  })
})

describe('ThemeToggle', () => {
  // One constant name, the state in aria-pressed: "Thème sombre, enfoncé" is
  // what a screen reader must say in dark — never "Thème clair, enfoncé".
  it('starts light: the dark theme, not pressed', () => {
    render(ThemeToggle)

    expect(screen.getByRole('button', { name: 'Thème sombre' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
  })

  it('switches to dark, and remembers it', async () => {
    render(ThemeToggle)
    const button = screen.getByRole('button', { name: 'Thème sombre' })

    await fireEvent.click(button)

    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(localStorage.getItem(THEME_KEY)).toBe('dark')
    expect(button).toHaveAccessibleName('Thème sombre')
    expect(button).toHaveAttribute('aria-pressed', 'true')
  })

  it('starts on the theme already applied', () => {
    document.documentElement.dataset.theme = 'dark'

    render(ThemeToggle)

    expect(screen.getByRole('button', { name: 'Thème sombre' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  })

  it('still switches when the storage refuses to write', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota')
    })
    render(ThemeToggle)

    await fireEvent.click(screen.getByRole('button', { name: 'Thème sombre' }))

    expect(document.documentElement.dataset.theme).toBe('dark')
  })
})
