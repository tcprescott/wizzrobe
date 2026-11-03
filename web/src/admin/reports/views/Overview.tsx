import { Link } from 'react-router-dom'
import { api, unwrap } from '../../../api/client'
import { Chip, type Tone } from '../../../components'
import { formatDayTime } from '../../../lib/time'
import type { ReportProps } from '../types'
import { useReport } from '../useReport'
import { Loaded } from './Loading'

const TONES: Record<string, Tone> = { ok: 'ok', pending: 'pending', bad: 'cancelled', neutral: 'neutral' }

/** The headline numbers, each linking to the report behind it. */
export function Overview({ range, zone, adminBase }: ReportProps) {
  const query = useReport(['overview', range.start, range.end], () =>
    api.GET('/api/admin/reports/overview', { params: { query: { start: range.start, end: range.end } } }).then(unwrap),
  )
  const search = `?from=${range.start}&to=${range.end}`
  return (
    <Loaded query={query} label="overview">
      {(kpis) => (
        <ul className="report-kpis" aria-label="Headline numbers">
          {kpis.map((k) => (
            <li key={k.key}>
              <Link className="report-kpi" to={`${adminBase}/reports/${k.report}${search}`}>
                <span className="report-kpi__label">{k.label}</span>
                <span className="report-kpi__value">{k.value}</span>
                <span className="report-kpi__detail">
                  {k.key === 'peak_players' && k.detail ? `at ${formatDayTime(k.detail, zone)}` : k.detail}
                </span>
                {k.tone !== 'neutral' && (
                  <Chip tone={TONES[k.tone] ?? 'neutral'}>
                    {k.tone === 'ok' ? 'Looks fine' : k.tone === 'bad' ? 'Needs a look' : 'Watch this'}
                  </Chip>
                )}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Loaded>
  )
}
