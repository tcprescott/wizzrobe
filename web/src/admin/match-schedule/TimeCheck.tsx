import { useEffect, useState } from 'react'
import { Button, Icon } from '../../components'
import { wallClock, zonedInstant } from '../../lib/time'
import { useScheduling, type BookingRef, type TimeCheck } from './data'

export interface When {
  date: string
  time: string
}

export function whenOf(iso: string | null | undefined, zone: string): When {
  return iso ? wallClock(iso, zone) : { date: '', time: '' }
}

export function instantOf(when: When, zone: string): string | null {
  return when.date && when.time ? zonedInstant(when.date, when.time, zone).toISOString() : null
}

/** Re-runs "Check this time" a moment after the time, tournament or players change. */
export function useTimeCheck({
  at,
  tournamentId,
  playerIds,
  matchId,
  ref,
}: {
  at: string | null
  tournamentId: number | null
  playerIds: number[]
  matchId?: number | null
  ref?: BookingRef
}): TimeCheck | null {
  const scheduling = useScheduling()
  const [check, setCheck] = useState<TimeCheck | null>(null)
  const players = playerIds.join(',')
  const refKey = ref ? `${ref.source}/${ref.key}` : ''
  useEffect(() => {
    setCheck(null)
    if (!at || !tournamentId) return
    let live = true
    const id = window.setTimeout(() => {
      scheduling
        .checkTime({
          tournament_id: tournamentId,
          scheduled_at: at,
          player_ids: players ? players.split(',').map(Number) : [],
          match_id: matchId ?? null,
          source: ref?.source ?? null,
          key: ref?.key ?? null,
        })
        .then((result) => live && setCheck(result))
        .catch(() => live && setCheck(null))
    }, 350)
    return () => {
      live = false
      window.clearTimeout(id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [at, tournamentId, players, matchId, refKey])
  return check
}

/** The date and time inputs, Suggest a time, and what "Check this time" found. */
export function WhenFields({
  when,
  onWhen,
  zone,
  check,
  onSuggest,
}: {
  when: When
  onWhen: (when: When) => void
  zone: string
  check: TimeCheck | null
  onSuggest: (() => Promise<void>) | null
}) {
  const [suggesting, setSuggesting] = useState(false)
  const [suggestFailure, setSuggestFailure] = useState<string | null>(null)
  async function suggest() {
    if (!onSuggest) return
    setSuggesting(true)
    setSuggestFailure(null)
    try {
      await onSuggest()
    } catch (err) {
      setSuggestFailure(err instanceof Error ? err.message : String(err))
    } finally {
      setSuggesting(false)
    }
  }
  const findings = [
    ...(check?.window ? [{ text: check.window.message, hard: !check.window.can_override }] : []),
    ...(check?.clashes ?? []).map((text) => ({ text, hard: false })),
  ]
  return (
    <div className="ms-stack">
      <div className="ms-when">
        <label className="field">
          <span className="field__label">Date</span>
          <input className="input" type="date" required value={when.date} onChange={(e) => onWhen({ ...when, date: e.target.value })} />
        </label>
        <label className="field">
          <span className="field__label">Time</span>
          <input className="input" type="time" required value={when.time} onChange={(e) => onWhen({ ...when, time: e.target.value })} />
        </label>
      </div>
      <span className="visually-hidden">Times are on {zone}.</span>
      {onSuggest && (
        <div>
          <Button variant="outline" icon="lightbulb" busy={suggesting} busyLabel="Finding a time…" onClick={suggest}>
            Suggest a time
          </Button>
          {suggestFailure && <p className="ms-note ms-warn">{suggestFailure}</p>}
        </div>
      )}
      {check && (
        <div aria-live="polite">
          <h3 className="ms-pane__section">Check this time</h3>
          {findings.length === 0 ? (
            <p className="ms-note ms-ok">
              <Icon name="check_circle" size={18} /> Nothing clashes.
            </p>
          ) : (
            <ul className="ms-findings">
              {findings.map((f) => (
                <li key={f.text} className={f.hard ? 'ms-finding ms-finding--hard' : 'ms-finding'}>
                  <Icon name={f.hard ? 'block' : 'error'} size={20} />
                  <span>
                    {f.text}
                    <span className="ms-note">{f.hard ? "You can't book outside it." : 'You can book anyway.'}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
