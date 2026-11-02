import { Link } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../../../api/client'
import { Chip, Panel } from '../../../components'
import { formatDayTime } from '../../../lib/time'
import { DataTable } from '../../kit'
import { hours } from '../range'
import type { ReportProps } from '../types'
import { useReport } from '../useReport'
import { Loaded, Stats } from './Loading'

type Contribution = Schemas['CrewContributionRow']
type Coverage = Schemas['CrewCoverageRow']

const role = (approved: number, required: number) => `${approved}/${required}`

/** Stream matches short of crew, then who covered what. */
export function Crew({ bounds, zone, adminBase }: ReportProps) {
  const query = useReport(['crew', bounds.start, bounds.end], () =>
    api.GET('/api/reports/crew-coverage', { params: { query: { start: bounds.start, end: bounds.end } } }).then(unwrap),
  )
  return (
    <Loaded query={query} label="crew activity">
      {(r) => {
        const streams = r.coverage_rows.filter((c) => c.is_stream_candidate)
        const gaps = streams.filter((c) => c.coverage_gap)
        return (
          <>
            <Stats
              items={[
                { label: 'Stream matches', value: streams.length },
                { label: 'Short of crew', value: gaps.length },
                { label: 'Crew members', value: r.contribution_rows.length },
              ]}
            />
            <Panel title={`Short of crew · ${gaps.length}`}>
              {gaps.length === 0 ? (
                <p className="report-note">Every stream match in this range has its crew.</p>
              ) : (
                <ul className="report-rows">
                  {gaps.map((c: Coverage) => (
                    <li key={c.match_id} className="report-row">
                      <Link to={`${adminBase}/match-schedule/${c.match_id}`} className="report-row__link">
                        {c.tournament_name} · Match #{c.match_id}
                      </Link>
                      <span className="report-row__grow report-note">
                        {c.scheduled_at ? formatDayTime(c.scheduled_at, zone) : 'Not scheduled'}
                        {c.stage ? ` · ${c.stage}` : ''}
                      </span>
                      <span className="report-chips">
                        {c.commentators_approved < c.commentators_required && (
                          <Chip tone="pending">Commentary {role(c.commentators_approved, c.commentators_required)}</Chip>
                        )}
                        {c.trackers_approved < c.trackers_required && (
                          <Chip tone="pending">Tracker {role(c.trackers_approved, c.trackers_required)}</Chip>
                        )}
                        {c.broadcasters_approved < c.broadcasters_required && (
                          <Chip tone="pending">Broadcast {role(c.broadcasters_approved, c.broadcasters_required)}</Chip>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
            <DataTable<Contribution>
              label="Crew by person"
              rows={[...r.contribution_rows].sort((a, b) => b.hours_covered - a.hours_covered)}
              rowKey={(c) => c.user_id ?? c.name}
              empty={{ icon: 'groups', title: 'No crew sign-ups in this range.' }}
              columns={[
                { key: 'name', header: 'Person', primary: true, cell: (c) => c.name },
                { key: 'comm', header: 'Commentary', align: 'end', cell: (c) => `${c.commentator_approved}/${c.commentator_total}` },
                { key: 'track', header: 'Tracker', align: 'end', cell: (c) => `${c.tracker_approved}/${c.tracker_total}` },
                { key: 'broad', header: 'Broadcast', align: 'end', cell: (c) => `${c.broadcaster_approved}/${c.broadcaster_total}` },
                { key: 'hours', header: 'Hours', align: 'end', cell: (c) => hours(c.hours_covered) },
              ]}
            />
          </>
        )
      }}
    </Loaded>
  )
}
