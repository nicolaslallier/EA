/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Origin of the backend API. See `frontend/.env.example`. */
  readonly VITE_API_BASE_URL?: string
  /** The Keycloak realm's issuer URL. See `frontend/.env.example`. */
  readonly VITE_AUTH_AUTHORITY?: string
  /** The SPA's public OIDC client id. See `frontend/.env.example`. */
  readonly VITE_AUTH_CLIENT_ID?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
