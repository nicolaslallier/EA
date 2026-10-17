// The colour theme: light unless this browser chose dark (docs/adr/0075).
//
// The choice is a per-browser convenience, so it lives in `localStorage` —
// which a private window or a blocked site may refuse, hence every access is
// guarded and a refusal simply means "light".
export type Theme = 'light' | 'dark'

export const THEME_KEY = 'ea.theme'

export function storedTheme(): Theme {
  try {
    return localStorage.getItem(THEME_KEY) === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

/** Puts the theme on `<html data-theme>`, which `style.css` keys on, and remembers it. */
export function setTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme
  try {
    localStorage.setItem(THEME_KEY, theme)
  } catch {
    // Not remembered, still applied.
  }
}

/** Called before mount, so a dark choice never flashes light first. */
export function applyStoredTheme(): void {
  document.documentElement.dataset.theme = storedTheme()
}
