import { Button } from '../../../components'

/** Newer/Older paging under a long list, with where you are. */
export function Pager({ total, limit, offset, onChange }: { total: number; limit: number; offset: number; onChange: (offset: number) => void }) {
  if (total <= limit) return null
  const last = Math.min(offset + limit, total)
  return (
    <nav className="report-pager" aria-label="Pages">
      <Button variant="outline" size="sm" icon="chevron_left" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
        Newer
      </Button>
      <span className="report-note">
        {offset + 1}–{last} of {total.toLocaleString()}
      </span>
      <Button variant="outline" size="sm" disabled={last >= total} onClick={() => onChange(offset + limit)}>
        Older
      </Button>
    </nav>
  )
}
