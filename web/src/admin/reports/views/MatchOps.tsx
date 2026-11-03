import { Link } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../../../api/client'
import { Chip, Panel } from '../../../components'
import { formatDayTime } from '../../../lib/time'
import { DataTable } from '../../kit'
import { axisStyle, baseOption, Chart } from '../Chart'
import { mins, num, pct } from '../range'
import type { ReportProps } from '../types'
import { useReport } from '../useReport'
import { Loaded, Stats } from './Loading'

type Aggregate = Schemas['MatchOperationsAggregate']
type Row = Schemas['MatchOperationsRow']

/** Start delays, durations and on-time rate per tournament, and the matches worth a look. */
export function MatchOps({ bounds, zone, adminBase }: ReportProps) {
  const query = useReport(['match-ops', bounds.start, bounds.end], () =>
    api.GET('/api/reports/match-operations', { params: { query: { start: bounds.start, end: bounds.end } } }).then(unwrap),
  )
  return (
    <Loaded query={query} label="match operations">
      {(r) => {
        const per = r.aggregates
        const total = per.reduce((n, a) => n + a.matches_total, 0)
        const started = per.reduce((n, a) => n + a.matches_started, 0)
        const finished = per.reduce((n, a) => n + a.matches_finished, 0)
        const flagged = new Set([...(r.flagged_match_ids ?? []), ...(r.duration_outlier_ids ?? [])])
        const worth = r.rows.filter((row) => flagged.has(row.match_id)).slice(0, 25)
        return (
          <>
            <Stats
              items={[
                { label: 'Matches', value: total },
                { label: 'Started', value: started },
                { label: 'Finished', value: finished },
              ]}
            />
            {per.length > 0 && (
              <Panel title="On time by tournament">
                <Chart
                  height={Math.max(160, per.length * 36 + 48)}
                  summary={`Share of matches started on time, per tournament: ${per.map((a) => `${a.tournament_name} ${pct(a.on_time_pct)}`).join(', ')}.`}
                  option={(p) => ({
                    ...baseOption(p),
                    legend: { show: false },
                    tooltip: { ...(baseOption(p).tooltip as object), valueFormatter: (v: number) => `${Math.round(v)}%` },
                    xAxis: { type: 'value', max: 100, ...axisStyle(p), axisLabel: { color: p.muted, formatter: '{value}%' } },
                    yAxis: { type: 'category', inverse: true, data: per.map((a) => a.tournament_name), ...axisStyle(p), splitLine: { show: false } },
                    series: [{ name: 'On time', type: 'bar', data: per.map((a) => a.on_time_pct ?? 0), itemStyle: { color: p.accent }, barMaxWidth: 20 }],
                  })}
                />
              </Panel>
            )}
            <DataTable<Aggregate>
              label="Match operations by tournament"
              rows={per}
              rowKey={(a) => a.tournament_id ?? a.tournament_name}
              empty={{ icon: 'timer', title: 'No matches in this range.' }}
              columns={[
                { key: 'name', header: 'Tournament', primary: true, cell: (a) => a.tournament_name },
                { key: 'matches', header: 'Done', align: 'end', cell: (a) => `${a.matches_finished}/${a.matches_total}` },
                { key: 'ontime', header: 'On time', align: 'end', cell: (a) => pct(a.on_time_pct) },
                { key: 'delay', header: 'Delay (min)', align: 'end', cell: (a) => num(a.avg_start_delay_min) },
                { key: 'length', header: 'Length (min)', align: 'end', cell: (a) => num(a.avg_duration_min) },
              ]}
            />
            {worth.length > 0 && (
              <Panel title={`Left out of the averages · ${flagged.size}`}>
                <ul className="report-rows">
                  {worth.map((row: Row) => (
                    <li key={row.match_id} className="report-row">
                      <Link to={`${adminBase}/match-schedule/${row.match_id}`} className="report-row__link">
                        {row.tournament_name} · Match #{row.match_id}
                      </Link>
                      <span className="report-row__grow report-note">
                        {row.scheduled_at ? formatDayTime(row.scheduled_at, zone) : 'Not scheduled'}
                        {row.stage ? ` · ${row.stage}` : ''}
                      </span>
                      {(r.duration_outlier_ids ?? []).includes(row.match_id) ? (
                        <Chip tone="pending">Length looks wrong ({mins(row.duration_min)})</Chip>
                      ) : (
                        <Chip tone="cancelled">Times look wrong</Chip>
                      )}
                    </li>
                  ))}
                </ul>
              </Panel>
            )}
          </>
        )
      }}
    </Loaded>
  )
}
