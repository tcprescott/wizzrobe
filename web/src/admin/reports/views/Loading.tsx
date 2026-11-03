import type { ReactNode } from 'react'
import { Banner, Button, Spinner } from '../../../components'
import type { QueryState } from '../../../api/useQuery'

/** The spinner, the error with Try again, or the report once its data is in. */
export function Loaded<T>({ query, label, children }: { query: QueryState<T>; label: string; children: (data: T) => ReactNode }) {
  if (query.data === undefined) {
    if (query.error)
      return (
        <Banner
          tone="error"
          action={
            <Button variant="outline" size="sm" onClick={query.reload}>
              Try again
            </Button>
          }
        >
          {query.error.message}
        </Banner>
      )
    return <Spinner label={`Loading ${label}`} />
  }
  return (
    <>
      {query.error && <Banner tone="error">{query.error.message}</Banner>}
      {children(query.data)}
    </>
  )
}

/** A row of headline numbers. */
export function Stats({ items }: { items: { label: string; value: ReactNode; detail?: ReactNode }[] }) {
  return (
    <dl className="report-stats">
      {items.map((s) => (
        <div key={s.label} className="report-stat">
          <dt>{s.label}</dt>
          <dd>
            <span className="report-stat__value">{s.value}</span>
            {s.detail && <span className="report-stat__detail">{s.detail}</span>}
          </dd>
        </div>
      ))}
    </dl>
  )
}
