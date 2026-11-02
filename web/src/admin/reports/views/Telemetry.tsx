import { useState } from 'react'
import { api, unwrap, type Schemas } from '../../../api/client'
import { DataTable, type Column } from '../../kit'
import type { ReportProps } from '../types'
import { useReport } from '../useReport'
import { Loaded, Stats } from './Loading'

type Dimension = 'paths' | 'controls' | 'users' | 'api'
type Row = Record<string, unknown>

const DIMENSIONS: { key: Dimension; label: string }[] = [
  { key: 'paths', label: 'Pages' },
  { key: 'controls', label: 'Buttons and tabs' },
  { key: 'users', label: 'People' },
  { key: 'api', label: 'API and MCP' },
]

const num = (v: unknown) => (typeof v === 'number' ? v.toLocaleString() : '—')

const COLUMNS: Record<Dimension, Column<Row>[]> = {
  paths: [
    { key: 'path', header: 'Page', primary: true, cell: (r) => String(r.path) },
    { key: 'views', header: 'Views', align: 'end', cell: (r) => num(r.views) },
    { key: 'users', header: 'People', align: 'end', cell: (r) => num(r.users) },
  ],
  controls: [
    { key: 'target', header: 'Control', primary: true, cell: (r) => String(r.target) },
    { key: 'path', header: 'Page', cell: (r) => (r.path ? String(r.path) : '—'), desktopOnly: true },
    { key: 'uses', header: 'Uses', align: 'end', cell: (r) => num(r.uses) },
    { key: 'users', header: 'People', align: 'end', cell: (r) => num(r.users) },
    { key: 'phone', header: 'On a phone', align: 'end', cell: (r) => num(r.phone), desktopOnly: true },
  ],
  users: [
    { key: 'user', header: 'Person', primary: true, cell: (r) => String(r.user) },
    { key: 'events', header: 'Events', align: 'end', cell: (r) => num(r.events) },
    { key: 'sessions', header: 'Sessions', align: 'end', cell: (r) => num(r.sessions) },
  ],
  api: [
    { key: 'target', header: 'Tool or route', primary: true, cell: (r) => String(r.target) },
    { key: 'surface', header: 'Via', cell: (r) => String(r.surface).toUpperCase() },
    { key: 'calls', header: 'Calls', align: 'end', cell: (r) => num(r.calls) },
    { key: 'errors', header: 'Errors', align: 'end', cell: (r) => num(r.errors) },
    { key: 'users', header: 'People', align: 'end', cell: (r) => num(r.users), desktopOnly: true },
  ],
}

/** How people use the tool in the range: totals, then the busiest pages, controls, people or API calls. */
export function Telemetry({ bounds }: ReportProps) {
  const [dimension, setDimension] = useState<Dimension>('paths')
  const summary = useReport(['telemetry', bounds.start, bounds.end], () =>
    api.GET('/api/telemetry/summary', { params: { query: { start: bounds.start, end: bounds.end } } }).then(unwrap),
  )
  const top = useReport(['telemetry-top', dimension, bounds.start, bounds.end], () =>
    api
      .GET('/api/telemetry/top', { params: { query: { dimension, start: bounds.start, end: bounds.end, limit: 25 } } })
      .then(unwrap) as Promise<Row[]>,
  )
  return (
    <>
      <Loaded query={summary} label="telemetry">
        {(s: Schemas['TelemetrySummaryResponse']) => (
          <Stats
            items={[
              { label: 'People', value: s.unique_users.toLocaleString() },
              { label: 'Sessions', value: s.unique_sessions.toLocaleString() },
              { label: 'Page views', value: s.page_views.toLocaleString() },
              { label: 'Everything', value: s.total_events.toLocaleString(), detail: `${s.rest_users + s.mcp_users + s.graphql_users} on the API` },
            ]}
          />
        )}
      </Loaded>
      <div className="admin-pills" role="group" aria-label="Busiest">
        {DIMENSIONS.map((d) => (
          <button key={d.key} type="button" className="admin-pill" aria-pressed={dimension === d.key} onClick={() => setDimension(d.key)}>
            {d.label}
          </button>
        ))}
      </div>
      <Loaded query={top} label="busiest rows">
        {(rows) => (
          <DataTable<Row>
            label={`Busiest ${DIMENSIONS.find((d) => d.key === dimension)!.label.toLowerCase()}`}
            rows={rows}
            rowKey={(r) => String(r.path ?? '') + String(r.target ?? '') + String(r.user_id ?? '') + String(r.surface ?? '') + String(r.event_type ?? '')}
            columns={COLUMNS[dimension]}
            empty={{ icon: 'query_stats', title: 'Nothing recorded in this range.' }}
          />
        )}
      </Loaded>
    </>
  )
}
