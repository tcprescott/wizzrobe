import type { ReactNode } from 'react'
import { Navigate, useParams } from 'react-router-dom'
import { EmptyState, Icon, PageHeader, type IconName } from '../../components'
import { ICONS } from '../../components/icons'
import { useCommunity } from '../../community/CommunityProvider'
import { usePhone } from '../../lib/usePhone'
import { DetailEmpty, SideListCard, SideListLayout, SideListRow, UnsavedChangesGuard, useAdmin } from '../kit'
import { ConnectedApps } from './panes/ConnectedApps'
import { Features } from './panes/Features'
import { General } from './panes/General'
import { Meetups, Volunteers } from './panes/Lead'
import { Members } from './panes/Members'
import { Watch } from './panes/Watch'

const PANES: Partial<Record<string, () => ReactNode>> = {
  general: () => <General />,
  members: () => <Members />,
  watch: () => <Watch />,
  volunteers: () => <Volunteers />,
  'community-events': () => <Meetups />,
  apps: () => <ConnectedApps />,
  features: () => <Features />,
}

function iconFor(name: string): IconName {
  return name in ICONS ? (name as IconName) : 'settings'
}

/**
 * Admin → Settings: the community's panes as a side list. Each pane is a stack
 * of cards that save on their own (Thomas's pick, A).
 */
export function SettingsScreen() {
  const { pane: slug } = useParams()
  const { nav, adminBase } = useAdmin()
  const { community } = useCommunity()
  const phone = usePhone()
  const base = `${adminBase}/settings`
  const panes = nav.settings_panes
  const pane = panes.find((p) => p.slug === slug)

  if (!slug && !phone && panes[0]) return <Navigate to={`${base}/${panes[0].slug}`} replace />

  const list = (
    <SideListCard label="Settings panes">
      {panes.map((p) => (
        <SideListRow
          key={p.slug}
          to={`${base}/${p.slug}`}
          title={p.label}
          sub={p.summary}
          leading={<Icon name={iconFor(p.icon)} />}
        />
      ))}
    </SideListCard>
  )

  let detail: ReactNode
  if (!slug) detail = <DetailEmpty>Pick a pane.</DetailEmpty>
  else if (!pane)
    detail = (
      <EmptyState icon="settings" title="You can't open that pane here">
        It may be switched off for this community, or need a role you don't have.
      </EmptyState>
    )
  else if (!PANES[pane.slug])
    detail = (
      <>
        <PageHeader title={pane.label} lede={pane.summary} />
        <EmptyState icon="info" title={`${pane.label} isn't in the app yet`}>
          Use the website for this one for now.
        </EmptyState>
      </>
    )
  else
    detail = (
      <div className="settings-stack">
        {phone && <PageHeader title={pane.label} lede={pane.summary} />}
        {PANES[pane.slug]?.()}
      </div>
    )

  return (
    <UnsavedChangesGuard>
      {(!phone || !slug) && <PageHeader title="Settings" lede={`Community-wide settings for ${community.name}`} />}
      <SideListLayout list={list} detail={detail} open={Boolean(slug)} backTo={base} backLabel="Settings" />
    </UnsavedChangesGuard>
  )
}
