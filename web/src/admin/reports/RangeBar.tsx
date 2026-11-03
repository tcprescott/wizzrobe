import { useState } from 'react'
import { Button } from '../../components'
import { rangeLabel, type DateRange, type ReportsMenu } from './range'

/** The jumps (whole event, each day) as pills, and a pair of date fields for anything else. */
export function RangeBar({
  menu,
  range,
  onChange,
}: {
  menu: ReportsMenu
  range: DateRange
  onChange: (next: DateRange) => void
}) {
  const [picking, setPicking] = useState(false)
  const [start, setStart] = useState(range.start)
  const [end, setEnd] = useState(range.end)
  const matched = menu.jumps.some((j) => j.start === range.start && j.end === range.end)
  const bad = !start || !end || end < start

  return (
    <div className="report-range">
      <div className="admin-pills" role="group" aria-label="Date range">
        {menu.jumps.map((j) => (
          <button
            key={j.label}
            type="button"
            className="admin-pill"
            aria-pressed={j.start === range.start && j.end === range.end}
            onClick={() => {
              setPicking(false)
              onChange({ start: j.start, end: j.end })
            }}
          >
            {j.label}
          </button>
        ))}
        <button
          type="button"
          className="admin-pill"
          aria-pressed={!matched}
          aria-expanded={picking}
          onClick={() => {
            setStart(range.start)
            setEnd(range.end)
            setPicking((v) => !v)
          }}
        >
          {matched ? 'Other dates' : rangeLabel(range)}
        </button>
      </div>
      {picking && (
        <form
          className="report-range__pick"
          onSubmit={(e) => {
            e.preventDefault()
            if (bad) return
            onChange({ start, end })
            setPicking(false)
          }}
        >
          <label className="field">
            <span className="field__label">From</span>
            <input className="input" type="date" value={start} onChange={(e) => setStart(e.target.value)} required />
          </label>
          <label className="field">
            <span className="field__label">To</span>
            <input className="input" type="date" value={end} min={start} onChange={(e) => setEnd(e.target.value)} required />
          </label>
          <Button type="submit" variant="outline" disabled={bad}>
            Show
          </Button>
        </form>
      )}
    </div>
  )
}
