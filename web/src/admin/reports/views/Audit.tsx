import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../../../api/client'
import { formatDayTime } from '../../../lib/time'
import type { ReportProps } from '../types'
import { useReport } from '../useReport'
import { Loaded } from './Loading'
import { Pager } from './Pager'
import { SearchBox } from './Search'

type Entry = Schemas['AuditRowResponse']
const LIMIT = 50

/** Every admin action in the range, newest first; open one for what changed. */
export function Audit({ range, zone, adminBase }: ReportProps) {
  const [area, setArea] = useState('')
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const query = useReport(['audit', range.start, range.end, area, search, offset], () =>
    api
      .GET('/api/admin/reports/audit', {
        params: {
          query: { start: range.start, end: range.end, area: area || undefined, search: search || undefined, limit: LIMIT, offset },
        },
      })
      .then(unwrap),
  )
  const areas = query.data?.areas ?? []
  return (
    <>
      <div className="report-filters">
        <label className="report-select">
          <span className="visually-hidden">Area</span>
          <select
            value={area}
            onChange={(e) => {
              setArea(e.target.value)
              setOffset(0)
            }}
          >
            <option value="">Every area</option>
            {areas.map((a) => (
              <option key={a.key} value={a.key}>
                {a.label}
              </option>
            ))}
          </select>
        </label>
        <SearchBox
          label="Search the audit log"
          value={search}
          onChange={(v) => {
            setSearch(v)
            setOffset(0)
          }}
        />
      </div>
      <Loaded query={query} label="audit log">
        {(page) =>
          page.items.length === 0 ? (
            <p className="report-note">Nothing in the audit log matches.</p>
          ) : (
            <>
              <ul className="report-log" aria-label="Audit log">
                {page.items.map((e) => (
                  <AuditItem key={e.id} entry={e} zone={zone} adminBase={adminBase} />
                ))}
              </ul>
              <Pager total={page.total} limit={LIMIT} offset={offset} onChange={setOffset} />
            </>
          )
        }
      </Loaded>
    </>
  )
}

function AuditItem({ entry: e, zone, adminBase }: { entry: Entry; zone: string; adminBase: string }) {
  const changes = e.changes ?? []
  const changedFields = e.changed_fields ?? []
  const facts = e.facts ?? []
  return (
    <li>
      <details className="report-log__item">
        <summary>
          <span className="report-log__head">
            <b>{e.label}</b>
            {e.subject && <span>{e.subject}</span>}
            <span className="report-log__when">{formatDayTime(e.created_at, zone)}</span>
          </span>
          <span className="report-log__sub">
            {e.actor_name}
            {e.summary ? ` · ${e.summary}` : ''}
          </span>
        </summary>
        <div className="report-log__body">
          {changes.length > 0 && (
            <table className="report-changes">
              <caption className="visually-hidden">What changed</caption>
              <thead>
                <tr>
                  <th scope="col">Field</th>
                  <th scope="col">Before</th>
                  <th scope="col">After</th>
                </tr>
              </thead>
              <tbody>
                {changes.map((c) => (
                  <tr key={c.field}>
                    <th scope="row">{c.field}</th>
                    <td>{c.before ?? '—'}</td>
                    <td>{c.after ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {changedFields.length > 0 && <p className="report-note">Changed: {changedFields.join(', ')}</p>}
          {facts.length > 0 && (
            <dl className="report-facts">
              {facts.map((f) => (
                <div key={f.label}>
                  <dt>{f.label}</dt>
                  <dd>{f.value}</dd>
                </div>
              ))}
            </dl>
          )}
          {e.match_id != null && (
            <Link to={`${adminBase}/match-schedule/${e.match_id}`} className="report-row__link">
              Open match #{e.match_id}
            </Link>
          )}
          {e.raw && (
            <details className="report-raw">
              <summary>Stored details</summary>
              <pre>{e.raw}</pre>
            </details>
          )}
        </div>
      </details>
    </li>
  )
}
