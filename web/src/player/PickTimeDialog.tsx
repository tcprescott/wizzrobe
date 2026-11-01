import { useEffect, useState } from 'react'
import { api, unwrap } from '../api/client'
import { Banner, FormDialog } from '../components'
import { wallClock, zonedInstant } from '../lib/time'
import type { Bookable } from './data'
import { useEventParam } from './data'

interface Props {
  item: Bookable
  zone: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onBooked: (message: string) => void
}

interface Check {
  refusal?: string | null
  clashes: string[]
}

/** Tomorrow at the next whole hour, a reasonable first guess the person changes. */
function firstGuess(zone: string): { date: string; time: string } {
  const at = new Date(Date.now() + 24 * 3600 * 1000)
  at.setUTCMinutes(0, 0, 0)
  return wallClock(at.toISOString(), zone)
}

/**
 * Pick a time for a bracket matchup. The time is checked as it changes: a
 * time the round's booking window refuses blocks the save, and anything that
 * only might not work (a player busy, a full room) is listed but allowed.
 */
export function PickTimeDialog({ item, zone, open, onOpenChange, onBooked }: Props) {
  const event = useEventParam()
  const [date, setDate] = useState(() => firstGuess(zone).date)
  const [time, setTime] = useState(() => firstGuess(zone).time)
  const [check, setCheck] = useState<Check | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const path = { source: item.source as 'bracket' | 'challonge' | 'startgg', key: item.key }

  useEffect(() => {
    if (!open || !date || !time) return
    let live = true
    setCheck(null)
    const at = zonedInstant(date, time, zone).toISOString()
    const timer = window.setTimeout(() => {
      api
        .GET('/api/me/bookable/{source}/{key}/check', { params: { path, query: { ...event, at } } })
        .then(unwrap)
        .then((result) => {
          if (live) setCheck(result)
        })
        .catch(() => {
          if (live) setCheck(null)
        })
    }, 300)
    return () => {
      live = false
      window.clearTimeout(timer)
    }
  }, [open, date, time, zone, item.source, item.key])

  async function book() {
    if (!date || !time) {
      setError('Choose a date and a time.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await api
        .POST('/api/me/bookable/{source}/{key}/book', {
          params: { path, query: event },
          body: { scheduled_at: zonedInstant(date, time, zone).toISOString() },
        })
        .then(unwrap)
      onOpenChange(false)
      onBooked(`Booked. ${item.opponent} gets a message with the time.`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const refused = Boolean(check?.refusal)
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Pick a time vs ${item.opponent}`}
      description={[item.tournament, item.round_label].filter(Boolean).join(' · ')}
      primaryLabel="Book it"
      busy={busy}
      busyLabel="Booking…"
      onPrimary={() => {
        if (!refused) void book()
      }}
      size="sm"
    >
      <div className="pick-time">
        {item.window_text && <p className="pick-time__window">{item.window_text}</p>}
        <div className="field-grid">
          <label className="field">
            <span className="field__label">Date</span>
            <input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
          </label>
          <label className="field">
            <span className="field__label">Time</span>
            <input className="input" type="time" value={time} onChange={(e) => setTime(e.target.value)} required />
          </label>
        </div>
        <p className="field__hint">Times are in {zone.replace(/_/g, ' ')}.</p>
        {check?.refusal && <Banner tone="error">{check.refusal}</Banner>}
        {check && !check.refusal && check.clashes.length > 0 && (
          <Banner tone="warning">
            <strong>This time might not work:</strong>
            <ul className="pick-time__clashes">
              {check.clashes.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
            You can still book it.
          </Banner>
        )}
        {check && !check.refusal && check.clashes.length === 0 && (
          <p className="pick-time__ok">Nothing clashes with this time.</p>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </FormDialog>
  )
}
