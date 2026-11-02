import { createContext, useContext } from 'react'
import { api, unwrap } from '../../api/client'
import { useQuery } from '../../api/useQuery'
import { useCommunity } from '../../community/CommunityProvider'
import type { AdminNav } from './types'

export function useAdminNavQuery() {
  const { community } = useCommunity()
  return useQuery(`admin/nav@${community.slug}`, () => api.GET('/api/admin/nav').then(unwrap))
}

export interface AdminContextValue {
  nav: AdminNav
  /** `<base>/admin`: area paths hang off this. */
  adminBase: string
  reload: () => void
}

export const AdminContext = createContext<AdminContextValue | null>(null)

/** The admin menu and its base path, for any screen inside an admin area. */
export function useAdmin(): AdminContextValue {
  const value = useContext(AdminContext)
  if (!value) throw new Error('useAdmin needs the admin layout')
  return value
}
