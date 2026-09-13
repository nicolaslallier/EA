import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    // Every interface, like the API (docs/adr/0016) and for the same reason:
    // the SPA is opened from other machines on the LAN. What this does *not*
    // decide is who the backend answers — that is EA_CORS_ORIGINS, which must
    // gain this machine's origin before those calls succeed. See docs/adr/0022.
    //
    // `server.allowedHosts` is deliberately left at its default: Vite already
    // answers only a loopback name or a literal IP, and refuses an arbitrary
    // domain resolved to this address. That is the same DNS-rebinding guard
    // EA_MCP_ALLOWED_HOSTS states for /mcp, and setting it to `true` here would
    // switch it off exactly when the server stops being loopback.
    host: true,
    port: 5173,
    // Fail loudly on a busy port instead of silently moving to 5174, which
    // would fall outside the backend's CORS allowlist.
    strictPort: true,
  },
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.spec.ts'],
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,vue}'],
    },
  },
})
