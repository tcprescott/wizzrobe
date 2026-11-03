import type { ReactNode } from 'react'
import { Navigate, useParams } from 'react-router-dom'
import type { Schemas } from '../../api/client'
import { EmptyState, Icon, PageHeader, type IconName } from '../../components'
import { ICONS } from '../../components/icons'
import { usePhone } from '../../lib/usePhone'
import { UnsavedChangesGuard } from './SettingsCard'
import { DetailEmpty, SideListCard, SideListLayout, SideListRow } from './SideList'

type Pane = Schemas['SettingsPaneResponse']

function iconFor(name: string): IconName {
  return name in ICONS ? (name as IconName) : 'settings'
}

/**
 * A settings screen: its panes as a side list beside the open one (phone: tap
 * in, back to the list). Each pane is a stack of cards that save on their own,
 * and leaving with unsaved changes asks first. Panes `render` doesn't know show
 * "isn't in the app yet".
 */
export function PaneScreen({
  title,
  lede,
  panes,
  base,
  render,
}: {
  title: string
  lede: string
  panes: Pane[]
  base: string
  render: Partial<Record<string, () => ReactNode>>
}) {
  const { pane: slug } = useParams()
  const phone = usePhone()
  const pane = panes.find((p) => p.slug === slug)

  if (!slug && !phone && panes[0]) return <Navigate to={`${base}/${panes[0].slug}`} replace />

  const list = (
    <SideListCard label={`${title} panes`}>
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

  const body = pane && render[pane.slug]
  let detail: ReactNode
  if (!slug) detail = <DetailEmpty>Pick a pane.</DetailEmpty>
  else if (!pane)
    detail = (
      <EmptyState icon="settings" title="You can't open that pane here">
        It may be switched off for this community, or need a role you don't have.
      </EmptyState>
    )
  else if (!body)
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
        {body()}
      </div>
    )

  return (
    <UnsavedChangesGuard>
      {(!phone || !slug) && <PageHeader title={title} lede={lede} />}
      <SideListLayout list={list} detail={detail} open={Boolean(slug)} backTo={base} backLabel={title} />
    </UnsavedChangesGuard>
  )
}
