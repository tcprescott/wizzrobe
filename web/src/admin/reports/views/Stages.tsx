import { api, unwrap, type Schemas } from '../../../api/client'
import { Banner, Panel } from '../../../components'
import { formatDay, formatDayTime, formatTime } from '../../../lib/time'
import { DataTable } from '../../kit'
import { axisStyle, baseOption, Chart } from '../Chart'
import { hours } from '../range'
import type { ReportProps } from '../types'
import { useReport } from '../useReport'
import { Loaded } from './Loading'

type StageRow = Schemas['StageUtilizationRow']

/** Each stage's bookings on one timeline, then hours, gaps and back-to-backs per stage. */
export function Stages({ range, bounds, zone }: ReportProps) {
  const query = useReport(['stages', range.start, range.end], () =>
    Promise.all([
      api.GET('/api/reports/stage-utilization', { params: { query: { start: bounds.start, end: bounds.end } } }).then(unwrap),
      api.GET('/api/admin/reports/stage-bookings', { params: { query: { start: range.start, end: range.end } } }).then(unwrap),
    ]),
  )
  return (
    <Loaded query={query} label="stage utilization">
      {([util, bookings]) => {
        const all = util.stages ?? []
        const stages = all.filter((s) => s.stage_id != null)
        const names = stages.map((s) => s.stage_name ?? `Stage ${s.stage_id}`)
        const bars = stages.flatMap((s, i) =>
          (bookings.stages[String(s.stage_id) as unknown as number] ?? []).map((b) => ({
            value: [i, Date.parse(b.start), Date.parse(b.end)],
            name: `${b.tournament_name} · Match #${b.match_id}`,
            label: `${formatDayTime(b.start, zone)} – ${formatDayTime(b.end, zone)}`,
          })),
        )
        return (
          <>
            {util.unplaced_candidate_count > 0 && (
              <Banner tone="warning">
                {util.unplaced_candidate_count === 1
                  ? '1 stream candidate has no stage yet.'
                  : `${util.unplaced_candidate_count} stream candidates have no stage yet.`}
              </Banner>
            )}
            {stages.length > 0 && (
              <Panel title="Bookings by stage">
                <Chart
                  height={Math.max(140, stages.length * 44 + 56)}
                  summary={`Stage bookings over the range: ${stages.map((s) => `${s.stage_name} ${s.match_count} matches`).join(', ')}.`}
                  option={(p) => ({
                    ...baseOption(p),
                    legend: { show: false },
                    tooltip: {
                      ...(baseOption(p).tooltip as object),
                      trigger: 'item',
                      formatter: (item: { data: { name: string; label: string } }) => `${item.data.name}<br/>${item.data.label}`,
                    },
                    xAxis: {
                      type: 'time',
                      min: Date.parse(bounds.start),
                      max: Date.parse(bounds.end),
                      ...axisStyle(p),
                      axisLabel: {
                        color: p.muted,
                        hideOverlap: true,
                        formatter: (v: number) =>
                          range.start === range.end
                            ? formatTime(new Date(v).toISOString(), zone)
                            : formatDay(new Date(v).toISOString(), zone),
                      },
                    },
                    yAxis: { type: 'category', inverse: true, data: names, ...axisStyle(p), splitLine: { show: false } },
                    series: [
                      {
                        type: 'custom',
                        encode: { x: [1, 2], y: 0 },
                        data: bars,
                        itemStyle: { color: p.accent },
                        renderItem: (
                          _params: unknown,
                          api: {
                            value: (i: number) => number
                            coord: (v: number[]) => number[]
                            size: (v: number[]) => number[]
                            style: () => object
                          },
                        ) => {
                          const row = api.value(0)
                          const [x0, y] = api.coord([api.value(1), row])
                          const [x1] = api.coord([api.value(2), row])
                          const h = api.size([0, 1])[1]! * 0.6
                          return {
                            type: 'rect',
                            shape: { x: x0, y: y! - h / 2, width: Math.max(2, x1! - x0!), height: h, r: 3 },
                            style: api.style(),
                          }
                        },
                      },
                    ],
                  })}
                />
              </Panel>
            )}
            <DataTable<StageRow>
              label="Stage utilization"
              rows={all}
              rowKey={(s) => s.stage_id ?? 'none'}
              empty={{ icon: 'tv', title: 'No stage bookings in this range.' }}
              columns={[
                { key: 'name', header: 'Stage', primary: true, cell: (s) => s.stage_name ?? 'No stage' },
                { key: 'matches', header: 'Matches', align: 'end', cell: (s) => s.match_count },
                { key: 'hours', header: 'Scheduled', align: 'end', cell: (s) => hours(s.scheduled_hours) },
                { key: 'gaps', header: 'Gaps', align: 'end', cell: (s) => hours(s.gap_hours) },
                { key: 'b2b', header: 'Back to back', align: 'end', cell: (s) => s.back_to_back_count },
              ]}
            />
          </>
        )
      }}
    </Loaded>
  )
}
