/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string
  readonly VITE_OAUTH_CLIENT_ID?: string
  readonly VITE_AUTH_ORIGIN?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
