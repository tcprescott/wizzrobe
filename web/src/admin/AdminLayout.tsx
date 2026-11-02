import { Navigate, NavLink, Outlet, useLocation } from 'react-router-dom'
import { Banner, Button, EmptyState, Icon, PageHeader, Spinner, type IconName } from '../components'
import { ICONS } from '../components/icons'
import { useCommunity } from '../community/CommunityProvider'
import { usePhone } from '../lib/usePhone'
import { AREA_ROUTES } from './kit/registry'
import { SectionNav, type SectionGroup } from './kit/SectionNav'
import type { AdminNav } from './kit/types'
import { AdminContext, useAdminNavQuery } from './kit/useAdminNav'
import './kit/admin.css'

function iconFor(name: string): IconName {
  return name in ICONS ? (name as IconName) : 'settings'
}

function groupsFor(nav: AdminNav, adminBase: string): SectionGroup[] {
  const groups: SectionGroup[] = []
  for (const area of nav.areas) {
    let group = groups.find((g) => g.title === area.group)
    if (!group) groups.push((group = { title: area.group, items: [] }))
    const left = area.slug === 'setup' ? nav.setup.filter((s) => s.required && !s.done).length : 0
    group.items.push({
      key: area.slug,
      label: area.label,
      icon: iconFor(area.icon),
      to: `${adminBase}/${area.slug}`,
      sub: !AREA_ROUTES.has(area.slug) ? 'Not in the app yet' : left ? `${left} ${left === 1 ? 'step' : 'steps'} left` : undefined,
      count: left,
    })
  }
  return groups
}

/**
 * Admin, layout A: a grouped list of every area you may open. A phone opens
 * an area as its own screen with a back link; a desktop keeps the list as a
 * column beside the open area.
 */
export function AdminLayout() {
  const { base } = useCommunity()
  const phone = usePhone()
  const { pathname } = useLocation()
  const { data, error, loading, reload } = useAdminNavQuery()
  const adminBase = `${base}/admin`
  const rest = pathname.startsWith(adminBase) ? pathname.slice(adminBase.length).replace(/^\//, '') : ''
  const [slug, ...tail] = rest.split('/')

  if (error && !data) {
    return (
      <>
        <PageHeader title="Admin" />
        <Banner
          tone="error"
          action={
            <Button variant="outline" onClick={reload}>
              Try again
            </Button>
          }
        >
          {error.message}
        </Banner>
      </>
    )
  }
  if (loading && !data) return <Spinner label="Loading admin" />
  if (!data) return null

  if (data.areas.length === 0) {
    return (
      <>
        <PageHeader title="Admin" />
        <EmptyState icon="admin_panel_settings" title="You don't have admin access here">
          If you think that's wrong, ask a staff member to check your role.
        </EmptyState>
      </>
    )
  }

  const alias = slug ? data.areas.find((a) => (a.aliases ?? []).includes(slug)) : undefined
  if (alias) return <Navigate to={[adminBase, alias.slug, ...tail].join('/')} replace />
  const first = data.areas[0]
  if (!slug && !phone && first) return <Navigate to={`${adminBase}/${first.slug}`} replace />

  const groups = groupsFor(data, adminBase)
  const context = { nav: data, adminBase, reload }

  if (phone) {
    return (
      <AdminContext.Provider value={context}>
        {slug ? (
          <>
            <NavLink to={adminBase} end className="admin-back">
              <Icon name="chevron_left" size={20} />
              Admin
            </NavLink>
            <Outlet />
          </>
        ) : (
          <>
            <NavLink to={`${base}/work`} className="admin-back">
              <Icon name="chevron_left" size={20} />
              Work
            </NavLink>
            <PageHeader title="Admin" />
            <SectionNav groups={groups} label="Admin areas" />
          </>
        )}
      </AdminContext.Provider>
    )
  }

  return (
    <AdminContext.Provider value={context}>
      <div className="admin-split">
        <SectionNav groups={groups} label="Admin areas" compact />
        <div className="admin-pane">
          <Outlet />
        </div>
      </div>
    </AdminContext.Provider>
  )
}
