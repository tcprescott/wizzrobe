import { Navigate, NavLink, Outlet, useLocation } from 'react-router-dom'
import { SectionNav, type SectionGroup } from '../admin/kit/SectionNav'
import { useAdminNavQuery } from '../admin/kit/useAdminNav'
import { Banner, Button, EmptyState, Icon, PageHeader, Spinner } from '../components'
import { useCommunity } from '../community/CommunityProvider'
import { effectiveZone } from '../lib/time'
import { usePhone } from '../lib/usePhone'
import { WORK_AREAS, type AreaContext } from './areas'
import { useWorkAreas, type WorkAreas } from './data'
import './work.css'
import '../admin/kit/admin.css'

function groupsFor(areas: WorkAreas, ctx: AreaContext, base: string): SectionGroup[] {
  const shown = WORK_AREAS.filter((a) => a.shown(areas, ctx))
  const toItem = (a: (typeof WORK_AREAS)[number]) => ({
    key: a.key,
    label: a.label,
    icon: a.icon,
    to: `${base}/${a.path}`,
    sub: a.sub?.(areas, ctx),
  })
  const groups: SectionGroup[] = []
  const eventDay = shown.filter((a) => a.key !== 'admin').map(toItem)
  if (eventDay.length) groups.push({ title: 'Event day', items: eventDay })
  const admin = shown.filter((a) => a.key === 'admin').map(toItem)
  if (admin.length) groups.push({ title: 'Running the community', items: admin })
  return groups
}

/**
 * Work, layout A: the areas your roles open, each with a live line. A phone
 * opens an area as its own screen with a back link; a desktop keeps the list
 * beside the open area. Admin is listed here but keeps its own pages.
 */
export function WorkLayout() {
  const { base } = useCommunity()
  const phone = usePhone()
  const { pathname } = useLocation()
  const { data, error, loading, reload } = useWorkAreas()
  const admin = useAdminNavQuery()
  const area = pathname.match(/\/work\/([^/]+)/)?.[1]

  if (error && !data) {
    return (
      <>
        <PageHeader title="Work" />
        <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
          {error.message}
        </Banner>
      </>
    )
  }
  if ((loading && !data) || !data) return <Spinner label="Loading your work" />

  const ctx: AreaContext = { zone: effectiveZone(data.zone), hasAdmin: (admin.data?.areas.length ?? 0) > 0, now: new Date() }
  const groups = groupsFor(data, ctx, base)
  const items = groups.flatMap((g) => g.items)

  if (items.length === 0) {
    return (
      <>
        <PageHeader title="Work" />
        <EmptyState icon="badge" title="Nothing to work on here">
          Your roles in this community don't open any event-day screens yet.
        </EmptyState>
      </>
    )
  }

  const firstWork = items.find((i) => i.key !== 'admin')
  if (!area && !phone && firstWork) return <Navigate to={firstWork.to} replace />
  if (area && !items.some((i) => i.key === area)) return <Navigate to={`${base}/work`} replace />

  if (phone) {
    return area ? (
      <>
        <NavLink to={`${base}/work`} end className="admin-back">
          <Icon name="chevron_left" size={20} />
          Work
        </NavLink>
        <Outlet context={{ areas: data, reloadAreas: reload }} />
      </>
    ) : (
      <>
        <PageHeader title="Work" lede="What your roles let you do here." />
        <SectionNav groups={groups} label="Work areas" />
      </>
    )
  }

  return (
    <div className="admin-split">
      <SectionNav groups={groups} label="Work areas" compact />
      <div className="admin-pane">
        <Outlet context={{ areas: data, reloadAreas: reload }} />
      </div>
    </div>
  )
}
