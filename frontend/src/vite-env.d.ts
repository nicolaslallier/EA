/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Origin of the backend API. See `frontend/.env.example`. */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
