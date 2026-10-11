// A fresh id for a note or a group, made in the browser: the object exists
// before the server has heard of it, and the server accepts the id it is given.

export function newId(): string {
  const crypto = globalThis.crypto as Crypto | undefined
  if (typeof crypto?.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  // ponytail: an environment without `crypto.randomUUID` (an old WebView) gets
  // Math.random — fine for a view object's id, which protects nothing.
  return 'xxxxxxxx-xxxx-4xxx-8xxx-xxxxxxxxxxxx'.replace(/x/g, () =>
    Math.floor(Math.random() * 16).toString(16),
  )
}
