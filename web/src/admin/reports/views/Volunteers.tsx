import { useState } from 'react'
import { api, unwrap, type Schemas } from '../../../api/client'
import { Chip } from '../../../components'
import { formatDayTime, formatTime } from '../../../lib/time'
import { DataTable } from '../../kit'
import type { ReportProps } from '../types'
import { useReport } from '../useReport'
import { Loaded, Stats } from './Loading'

type Shift = Schemas['VolunteerCoverageRow']

/** Filled against needed per shift, short shifts first unless you ask for all of them. */
export function Volunteers({ range, zone }: ReportProps) {
  const [shortOnly, setShortOnly] = useState(true)
  const query = useReport(['volunteers', range.start, range.end], () =>
    api.GET('/api/admin/reports/volunteer-coverage', { params: { query: { start: range.start, end: range.end } } }).then(unwrap),
  )
  return (
    <Loaded query={query} label="volunteer coverage">
      {(rows) => {
        const short = rows.filter((r) => r.understaffed)
        const filled = rows.reduce((n, r) => n + Math.min(r.filled, r.needed), 0)
        const needed = rows.reduce((n, r) => n + r.needed, 0)
        const shown = shortOnly ? short : rows
        return (
          <>
            <Stats
              items={[
                { label: 'Shifts', value: rows.length },
                { label: 'Short', value: short.length },
                { label: 'Spots filled', value: `${filled} / ${needed}` },
              ]}
            />
            <div className="admin-pills" role="group" aria-label="Which shifts">
              <button type="button" className="admin-pill" aria-pressed={shortOnly} onClick={() => setShortOnly(true)}>
                Short · {short.length}
              </button>
              <button type="button" className="admin-pill" aria-pressed={!shortOnly} onClick={() => setShortOnly(false)}>
                All shifts
              </button>
            </div>
            <DataTable<Shift>
              label="Volunteer shifts"
              rows={shown}
              rowKey={(r) => r.shift_id}
              empty={{
                icon: 'volunteer_activism',
                title: shortOnly ? 'No shift is short.' : 'No shifts in this range.',
              }}
              columns={[
                { key: 'pos', header: 'Position', primary: true, cell: (r) => (r.label ? `${r.position} · ${r.label}` : r.position) },
                { key: 'when', header: 'When', cell: (r) => `${formatDayTime(r.starts_at, zone)} – ${formatTime(r.ends_at, zone)}` },
                {
                  key: 'filled',
                  header: 'Filled',
                  align: 'end',
                  cell: (r) => <Chip tone={r.understaffed ? 'pending' : 'ok'}>{`${r.filled} of ${r.needed}`}</Chip>,
                },
                { key: 'drafts', header: 'Drafts', align: 'end', cell: (r) => r.drafts, desktopOnly: true },
                { key: 'ack', header: 'Confirmed', align: 'end', cell: (r) => r.acknowledged, desktopOnly: true },
              ]}
            />
          </>
        )
      }}
    </Loaded>
  )
}
