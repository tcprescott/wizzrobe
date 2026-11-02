import type { AdminAreaRoute } from './types'

// Each admin area folder registers itself by exporting `areas` from its own
// routes.tsx; nothing else lists them, so parallel area work never touches a shared file.
const modules = import.meta.glob<{ areas: AdminAreaRoute[] }>('../*/routes.tsx', { eager: true })

export const AREA_ROUTES: ReadonlyMap<string, AdminAreaRoute> = new Map(
  Object.values(modules)
    .flatMap((m) => m.areas)
    .map((area) => [area.slug, area]),
)
