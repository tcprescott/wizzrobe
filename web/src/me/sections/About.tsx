import { useMemo, useState } from 'react'
import { api, unwrap } from '../../api/client'
import { Button, Panel, PanelRow } from '../../components'
import { deviceZone } from '../../lib/time'
import { message } from '../data'
import { useProfile } from '../MeLayout'

const CYCLES = [
  { value: '', label: 'Auto' },
  { value: '12', label: '12-hour' },
  { value: '24', label: '24-hour' },
]

function zones(current: string | null | undefined): string[] {
  const all: string[] =
    typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : [deviceZone()]
  return current && !all.includes(current) ? [current, ...all] : all
}

/** Name, pronouns, clock and (where the community pays out) the Matcherino handle. */
export function About() {
  const { profile, reload } = useProfile()
  const [name, setName] = useState(profile.display_name ?? '')
  const [pronouns, setPronouns] = useState(profile.pronouns ?? '')
  const [matcherino, setMatcherino] = useState(profile.matcherino_username ?? '')
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const options = useMemo(() => zones(profile.timezone), [profile.timezone])

  const dirty =
    name !== (profile.display_name ?? '') ||
    pronouns !== (profile.pronouns ?? '') ||
    (profile.payouts && matcherino !== (profile.matcherino_username ?? ''))

  async function save() {
    setBusy(true)
    setError(null)
    try {
      await api
        .PATCH('/api/users/me', {
          body: {
            display_name: name,
            pronouns,
            ...(profile.payouts ? { matcherino_username: matcherino } : {}),
          },
        })
        .then(unwrap)
      setSaved('Saved.')
      reload()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  async function setClock(body: { timezone?: string | null; hour_cycle?: string | null }) {
    setError(null)
    try {
      await api.PATCH('/api/me/clock', { body }).then(unwrap)
      setSaved('Saved.')
      reload()
    } catch (err) {
      setError(message(err))
    }
  }

  return (
    <div className="me-stack">
      <Panel title="About you">
        <PanelRow>
          <form
            className="me-form"
            style={{ width: '100%' }}
            onSubmit={(e) => {
              e.preventDefault()
              void save()
            }}
          >
            <label className="field">
              <span className="field__label">Display name</span>
              <input
                className="input"
                value={name}
                maxLength={64}
                placeholder={profile.discord_username ?? ''}
                onChange={(e) => setName(e.target.value)}
              />
              <span className="field__hint">Leave it empty to go by your Discord name.</span>
            </label>
            <label className="field">
              <span className="field__label">Pronouns</span>
              <input className="input" value={pronouns} maxLength={40} onChange={(e) => setPronouns(e.target.value)} />
            </label>
            {profile.payouts && (
              <label className="field">
                <span className="field__label">Matcherino handle</span>
                <input
                  className="input"
                  value={matcherino}
                  placeholder="e.g. yourname#123456"
                  onChange={(e) => setMatcherino(e.target.value)}
                />
                <span className="field__hint">
                  Where prize money is sent, and how your event ticket gets linked to you at check-in. Copy it from your
                  Matcherino profile, including the number after the hash.
                </span>
              </label>
            )}
            <div>
              <Button type="submit" variant="primary" disabled={!dirty} busy={busy} busyLabel="Saving…">
                Save
              </Button>
            </div>
          </form>
        </PanelRow>
      </Panel>
      <Panel title="Time">
        <PanelRow>
          <div className="me-form" style={{ width: '100%' }}>
            <label className="field">
              <span className="field__label">Your timezone</span>
              <select
                className="input"
                value={profile.timezone ?? ''}
                onChange={(e) => void setClock({ timezone: e.target.value || null })}
              >
                <option value="">Follow this device ({deviceZone().replace(/_/g, ' ')})</option>
                {options.map((z) => (
                  <option key={z} value={z}>
                    {z.replace(/_/g, ' ')}
                  </option>
                ))}
              </select>
              {profile.zone_pinned && (
                <span className="field__hint">
                  This community shows every time in {profile.community_zone.replace(/_/g, ' ')}, so your zone only
                  changes other communities.
                </span>
              )}
            </label>
            <div className="field">
              <span className="field__label" id="clock-label">
                Clock
              </span>
              <div className="segmented" role="radiogroup" aria-labelledby="clock-label">
                {CYCLES.map((c) => (
                  <span key={c.value}>
                    <input
                      type="radio"
                      id={`clock-${c.value || 'auto'}`}
                      name="clock"
                      checked={(profile.hour_cycle ?? '') === c.value}
                      onChange={() => void setClock({ hour_cycle: c.value || null })}
                    />
                    <label htmlFor={`clock-${c.value || 'auto'}`}>{c.label}</label>
                  </span>
                ))}
              </div>
            </div>
          </div>
        </PanelRow>
      </Panel>
      {saved && !error && (
        <p className="me-saved" role="status">
          {saved}
        </p>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
