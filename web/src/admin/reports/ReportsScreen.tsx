import type { ComponentType } from 'react'
import { Link, Navigate, useLocation, useParams, useSearchParams } from 'react-router-dom'
import { api, unwrap } from '../../api/client'
import { Banner, Button, Icon, PageHeader, Spinner, type IconName } from '../../components'
import { usePhone } from '../../lib/usePhone'
import { DetailEmpty, SideListCard, SideListLayout, SideListRow, useAdmin } from '../kit'
import { RangeBar } from './RangeBar'
import { rangeBounds, useRange } from './range'
import type { ReportProps } from './types'
import { useReport } from './useReport'
import { Audit } from './views/Audit'
import { Capacity } from './views/Capacity'
import { Crew } from './views/Crew'
import { Exports } from './views/Exports'
import { Insights } from './views/Insights'
import { MatchOps } from './views/MatchOps'
import { Messages } from './views/Messages'
import { Overview } from './views/Overview'
import { Stages } from './views/Stages'
import { Telemetry } from './views/Telemetry'
import { Volunteers } from './views/Volunteers'
import './reports.css'

interface View {
  component: ComponentType<ReportProps>
  /** Whether the report reads the shared date range (Insights and Exports keep their own). */
  ranged: boolean
}

const VIEWS: Record<string, View> = {
  overview: { component: Overview, ranged: true },
  insights: { component: Insights, ranged: false },
  capacity: { component: Capacity, ranged: true },
  match_ops: { component: MatchOps, ranged: true },
  crew: { component: Crew, ranged: true },
  stages: { component: Stages, ranged: true },
  volunteers: { component: Volunteers, ranged: true },
  audit: { component: Audit, ranged: true },
  telemetry: { component: Telemetry, ranged: true },
  messages: { component: Messages, ranged: true },
  exports: { component: Exports, ranged: true },
}

const ICONS: Record<string, IconName> = { insights: 'trending_up' }
const icon = (name: string) => (ICONS[name] ?? name) as IconName

const SHORT: Record<string, string> = {
  overview: 'Headline numbers',
  insights: 'Crew, volunteer hours and tournament health over weeks or months',
  capacity: 'Players at once across the event vs capacity',
  match_ops: 'Start delays, durations, on-time rate',
  crew: 'Commentator and tracker coverage',
  stages: 'Hours per stage, gaps, unplaced',
  volunteers: 'Filled vs needed per shift',
  audit: 'Every admin action, filterable',
  telemetry: 'Page views, interactions, events',
  messages: 'Every DM and whether it arrived',
  exports: 'CSV and JSON downloads from the last 3 days',
}

/** Admin › Reports, layout C: the report list beside the open report on desktop; each report its own screen on a phone. */
export function ReportsScreen() {
  const { report } = useParams()
  const [params] = useSearchParams()
  const location = useLocation()
  const phone = usePhone()
  const { adminBase } = useAdmin()
  const base = `${adminBase}/reports`
  const menuQuery = useReport(['menu'], () => api.GET('/api/admin/reports').then(unwrap))
  const menu = menuQuery.data
  const [range, setRange] = useRange(menu)

  const legacy = params.get('report')
  if (!report && legacy) {
    const rest = new URLSearchParams(params)
    rest.delete('report')
    const query = rest.toString()
    return <Navigate replace to={`${base}/${legacy}${query ? `?${query}` : ''}`} />
  }

  if (!menu || !range) {
    return (
      <>
        <PageHeader title="Reports" />
        {menuQuery.error ? (
          <Banner
            tone="error"
            action={
              <Button variant="outline" size="sm" onClick={menuQuery.reload}>
                Try again
              </Button>
            }
          >
            {menuQuery.error.message}
          </Banner>
        ) : (
          <Spinner label="Loading reports" />
        )}
      </>
    )
  }

  const entries = [
    ...(menu.overview
      ? [{ key: 'overview', title: 'Overview', icon: 'dashboard', description: 'The headline numbers for the range, each linking to its report.' }]
      : []),
    ...menu.reports,
  ]
  const fallback = entries[0]?.key
  const openKey = report ?? (phone ? null : fallback)
  const open = entries.find((e) => e.key === openKey)
  const view = open ? VIEWS[open.key] : undefined
  const keep = location.search

  const list = (
    <SideListCard label="Reports">
      {entries.map((e) => (
        <SideListRow
          key={e.key}
          to={`${base}/${e.key}${keep}`}
          leading={<Icon name={icon(e.icon)} />}
          title={e.title}
          sub={SHORT[e.key] ?? e.description}
        />
      ))}
    </SideListCard>
  )

  let detail = null
  if (report && !open) {
    detail = <Banner tone="warning">That report isn't here, or isn't one you can open.</Banner>
  } else if (open && view) {
    const View = view.component
    const props: ReportProps = { menu, range, bounds: rangeBounds(range, menu.zone), zone: menu.zone, adminBase }
    detail = (
      <section className="report" aria-labelledby={phone ? undefined : 'report-title'}>
        {!phone && (
          <header className="report__head">
            <h2 id="report-title">{open.title}</h2>
            <p className="report-note">{open.description}</p>
          </header>
        )}
        {view.ranged && <RangeBar menu={menu} range={range} onChange={setRange} />}
        <View {...props} />
      </section>
    )
  } else if (!phone) {
    detail = <DetailEmpty>Pick a report.</DetailEmpty>
  }

  if (phone && report !== undefined) {
    return (
      <>
        <Link to={`${base}${keep}`} className="admin-back">
          <Icon name="chevron_left" />
          Reports
        </Link>
        <PageHeader title={open?.title ?? 'Reports'} lede={open?.description} />
        {detail}
      </>
    )
  }

  return (
    <>
      <PageHeader title="Reports" />
      <SideListLayout list={list} detail={detail} open={false} backTo={`${base}${keep}`} backLabel="Reports" />
    </>
  )
}
