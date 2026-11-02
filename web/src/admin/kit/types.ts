import type { ReactNode } from 'react'
import type { Schemas } from '../../api/client'

export type AdminNav = Schemas['AdminNavResponse']
export type AdminAreaInfo = Schemas['AdminAreaResponse']

/**
 * One admin area's screen, as its folder registers it in `routes.tsx`:
 *
 *     export const areas: AdminAreaRoute[] = [{ slug: 'tournaments', element: <TournamentsScreen /> }]
 *
 * The label, icon, group and who may open it come from `GET /api/admin/nav`;
 * the folder only supplies the screen. `element` renders under
 * `/admin/<slug>/*`, so an area with sub-pages uses its own relative `<Routes>`.
 */
export interface AdminAreaRoute {
  slug: string
  element: ReactNode
}
