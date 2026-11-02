/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string
  readonly VITE_OAUTH_CLIENT_ID?: string
  readonly VITE_AUTH_ORIGIN?: string
  /** Set by vite.config.ts from WIZZROBE_APP_ID (see src/native/appId.ts). */
  readonly VITE_APP_ID: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
