import { api, unwrap } from '../../../api/client'
import { Chip, Panel } from '../../../components'
import { formatDayTime, formatTime } from '../../../lib/time'
import { axisStyle, baseOption, Chart } from '../Chart'
import type { ReportProps } from '../types'
import { useReport } from '../useReport'
import { Loaded, Stats } from './Loading'

/** Players at once across the range against the configured capacity, then the busiest moments. */
export function Capacity({ range, zone }: ReportProps) {
  const query = useReport(['capacity', range.start, range.end], () =>
    api.GET('/api/admin/reports/capacity', { params: { query: { start: range.start, end: range.end } } }).then(unwrap),
  )
  return (
    <Loaded query={query} label="capacity">
      {(s) => {
        const points = s.times.map((t, i) => ({ t, players: s.players[i] ?? 0, on: s.on_stream[i] ?? 0 }))
        const peak = points.reduce((a, b) => (b.players > a.players ? b : a), points[0] ?? { t: '', players: 0, on: 0 })
        const busiest = [...points].sort((a, b) => b.players - a.players).filter((p) => p.players > 0).slice(0, 5)
        const share = (n: number) => (s.max_capacity ? Math.round((n * 100) / s.max_capacity) : null)
        return (
          <>
            <Stats
              items={[
                { label: 'Peak', value: `${peak.players} / ${s.max_capacity}`, detail: peak.t ? formatDayTime(peak.t, zone) : 'No matches' },
                { label: 'Capacity', value: s.max_capacity, detail: 'players at once' },
              ]}
            />
            <Panel title="Players at once">
              <Chart
                summary={`Players at once, peaking at ${peak.players} against a capacity of ${s.max_capacity}.`}
                option={(p) => ({
                  ...baseOption(p),
                  xAxis: {
                    type: 'category',
                    data: s.times.map((t) => (range.start === range.end ? formatTime(t, zone) : formatDayTime(t, zone))),
                    ...axisStyle(p),
                    splitLine: { show: false },
                  },
                  yAxis: { type: 'value', minInterval: 1, ...axisStyle(p) },
                  series: [
                    {
                      name: 'On a stage',
                      type: 'bar',
                      stack: 'players',
                      data: s.on_stream,
                      itemStyle: { color: p.accent },
                      barCategoryGap: '10%',
                    },
                    {
                      name: 'Off stage',
                      type: 'bar',
                      stack: 'players',
                      data: s.off_stream,
                      itemStyle: { color: p.info },
                      markLine: {
                        symbol: 'none',
                        silent: true,
                        lineStyle: { color: p.bad, type: 'dashed' },
                        label: { formatter: `Capacity ${s.max_capacity}`, color: p.bad, position: 'insideEndTop' },
                        data: [{ yAxis: s.max_capacity }],
                      },
                    },
                  ],
                })}
              />
            </Panel>
            <Panel title="Busiest moments">
              {busiest.length === 0 ? (
                <p className="report-note">No matches in this range.</p>
              ) : (
                <ul className="report-rows">
                  {busiest.map((b) => {
                    const used = share(b.players)
                    return (
                      <li key={b.t} className="report-row">
                        <b>{formatDayTime(b.t, zone)}</b>
                        <span className="report-row__grow">
                          {b.players} players, {b.on} on a stage
                        </span>
                        {used != null && (
                          <Chip tone={used > 100 ? 'cancelled' : used >= 85 ? 'pending' : 'ok'}>{used}% of capacity</Chip>
                        )}
                      </li>
                    )
                  })}
                </ul>
              )}
            </Panel>
          </>
        )
      }}
    </Loaded>
  )
}
