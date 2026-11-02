import { useState } from 'react'
import { api, unwrap, type Schemas } from '../../../api/client'
import { Panel } from '../../../components'
import { DataTable } from '../../kit'
import { axisStyle, baseOption, Chart, type Palette } from '../Chart'
import { addDays, hours, pct, rangeBounds, rangeLabel } from '../range'
import type { ReportProps } from '../types'
import { useReport } from '../useReport'
import { Loaded, Stats } from './Loading'

type Bucket = 'week' | 'month'
type Health = Schemas['TournamentHealthRow']

const CATEGORY_LABELS: Record<string, string> = {
  page: 'Page views',
  interaction: 'Interactions',
  event: 'Events',
  api: 'API calls',
}

function lines(p: Palette, labels: string[], series: { name: string; data: (number | null)[]; color: string; bar?: boolean }[]) {
  return {
    ...baseOption(p),
    xAxis: { type: 'category', data: labels, ...axisStyle(p), splitLine: { show: false } },
    yAxis: { type: 'value', minInterval: 1, ...axisStyle(p) },
    series: series.map((s) =>
      s.bar
        ? { name: s.name, type: 'bar', data: s.data, itemStyle: { color: s.color }, barMaxWidth: 24 }
        : { name: s.name, type: 'line', data: s.data, itemStyle: { color: s.color }, lineStyle: { color: s.color, width: 2 }, symbolSize: 6 },
    ),
  }
}

/** Activity, crew, volunteer hours and tournament health over the community's whole history, by week or month. */
export function Insights({ zone }: ReportProps) {
  const [bucket, setBucket] = useState<Bucket>('week')
  const extent = useReport(['extent'], () => api.GET('/api/admin/reports/activity-extent').then(unwrap))
  const today = new Date().toISOString().slice(0, 10)
  const first = extent.data?.first ?? addDays(today, -90)
  const last = extent.data?.last ?? today
  const range = { start: first < addDays(last, -730) ? addDays(last, -730) : first, end: last }
  const b = rangeBounds(range, zone)
  const query = useReport(extent.data ? ['insights', bucket, b.start, b.end] : null, async () => {
    const q = { start: b.start, end: b.end, bucket }
    const [activity, crew, volunteers, health] = await Promise.all([
      api.GET('/api/reports/activity-trends', { params: { query: q } }).then(unwrap),
      api.GET('/api/reports/crew-participation-trends', { params: { query: q } }).then(unwrap),
      api.GET('/api/reports/volunteer-hour-trends', { params: { query: q } }).then(unwrap).catch(() => null),
      api.GET('/api/reports/tournament-health', { params: { query: { start: b.start, end: b.end } } }).then(unwrap),
    ])
    return { activity, crew, volunteers, health }
  })

  return (
    <>
      <div className="report-range">
        <div className="admin-pills" role="group" aria-label="Group by">
          {(['week', 'month'] as const).map((value) => (
            <button key={value} type="button" className="admin-pill" aria-pressed={bucket === value} onClick={() => setBucket(value)}>
              By {value}
            </button>
          ))}
        </div>
        <p className="report-note">{extent.data ? rangeLabel(range) : 'Finding the first activity…'}</p>
      </div>
      <Loaded query={query} label="insights">
        {({ activity, crew, volunteers, health }) => (
          <>
            <Stats
              items={[
                { label: 'Activity', value: activity.total.toLocaleString() },
                { label: 'Crew people', value: crew.totals.unique_people, detail: `${crew.totals.commentator_approved + crew.totals.tracker_approved} approved sign-ups` },
                ...(volunteers
                  ? [{ label: 'Volunteer hours', value: hours(volunteers.totals.scheduled_hours), detail: `${volunteers.totals.volunteers} volunteers` }]
                  : []),
              ]}
            />
            <Panel title="Activity">
              <Chart
                summary={`Activity per ${bucket}, ${activity.total} in all.`}
                option={(p) => ({
                  ...lines(p, activity.bucket_labels, []),
                  series: activity.categories.map((c, i) => ({
                    name: CATEGORY_LABELS[c.category] ?? c.category,
                    type: 'bar',
                    stack: 'all',
                    data: c.counts,
                    itemStyle: { color: [p.accent, p.info, p.live, p.pending, p.ok][i % 5] },
                    barMaxWidth: 28,
                  })),
                })}
              />
            </Panel>
            <Panel title="Crew sign-ups">
              <Chart
                summary={`Crew sign-ups per ${bucket}: ${crew.totals.commentator_signups} commentary and ${crew.totals.tracker_signups} tracker.`}
                option={(p) =>
                  lines(p, crew.bucket_labels, [
                    { name: 'Commentary', data: crew.commentator_signups, color: p.accent },
                    { name: 'Tracker', data: crew.tracker_signups, color: p.info },
                    { name: 'People', data: crew.unique_people, color: p.live },
                  ])
                }
              />
            </Panel>
            {volunteers && (
              <Panel title="Volunteer hours">
                <Chart
                  summary={`Volunteer hours per ${bucket}: ${hours(volunteers.totals.scheduled_hours)} scheduled of ${hours(volunteers.totals.needed_hours)} needed.`}
                  option={(p) =>
                    lines(p, volunteers.bucket_labels, [
                      { name: 'Needed', data: volunteers.needed_hours, color: p.muted, bar: true },
                      { name: 'Scheduled', data: volunteers.scheduled_hours, color: p.accent, bar: true },
                      { name: 'Checked in', data: volunteers.checked_in_hours, color: p.ok, bar: true },
                    ])
                  }
                />
              </Panel>
            )}
            <DataTable<Health>
              label="Tournament health"
              rows={[...health].sort((x, y) => (x.health_score ?? 101) - (y.health_score ?? 101))}
              rowKey={(t) => t.tournament_id}
              empty={{ icon: 'trophy', title: 'No tournaments with matches yet.' }}
              columns={[
                { key: 'name', header: 'Tournament', primary: true, cell: (t) => t.tournament_name },
                { key: 'score', header: 'Health', align: 'end', cell: (t) => (t.health_score == null ? '—' : Math.round(t.health_score)) },
                { key: 'done', header: 'Done', align: 'end', cell: (t) => pct(t.completion_pct) },
                { key: 'ontime', header: 'On time', align: 'end', cell: (t) => pct(t.on_time_pct) },
                { key: 'cover', header: 'Crew', align: 'end', cell: (t) => pct(t.coverage_pct) },
              ]}
            />
          </>
        )}
      </Loaded>
    </>
  )
}
