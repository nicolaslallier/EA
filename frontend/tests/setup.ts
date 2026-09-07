import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/vue'
import { afterEach } from 'vitest'

// Vitest runs with `globals: false`, so Testing Library cannot install its own
// auto-cleanup: without this, a second `render` stacks on top of the first and
// every query finds two of everything.
afterEach(cleanup)
