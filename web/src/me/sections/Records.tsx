import { useState } from 'react'
import { api, unwrap } from '../../api/client'
import { useCommunity } from '../../community/CommunityProvider'
import { Button, Chip, FormDialog, Panel, PanelRow, Spinner, type Tone } from '../../components'
import { done, message, useSection } from '../data'
import { useProfile } from '../MeLayout'

const STATUS: Record<string, { label: string; tone: Tone }> = {
  new: { label: 'New', tone: 'pending' },
  acknowledged: { label: 'Acknowledged', tone: 'live' },
  arranged: { label: 'Arranged', tone: 'ok' },
  withdrawn: { label: 'Withdrawn', tone: 'neutral' },
}

function privacy(community: string): string {
  const staff = community ? `${community} staff` : 'community staff'
  return (
    `Only ${staff} can read these notes. Once a request is arranged, proctors see an "ADA:" line with your name on ` +
    "their match run sheets, and a button in the match's Ready to play? checklist, but not what the notes say. We " +
    "can't guarantee notes stay private, so please don't put anything sensitive or personal here. If it is " +
    `sensitive, contact ${staff} directly instead.`
  )
}

export function Accommodation() {
  const { community } = useCommunity()
  const { reload: reloadProfile } = useProfile()
  const { data, reload } = useSection('accommodation', () => api.GET('/api/me/accommodation').then(unwrap))
  const [asking, setAsking] = useState<boolean | null>(null)
  const [details, setDetails] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  if (!data) return <Spinner label="Loading your request" />
  const requested = asking ?? data.requested
  const text = details ?? data.details ?? ''

  async function save(want: boolean) {
    setBusy(true)
    setError(null)
    try {
      await api.PUT('/api/me/accommodation', { body: { requested: want, details: want ? text : null } }).then(unwrap)
      setSaved(want ? 'Saved. Staff have been told.' : 'Request withdrawn.')
      setAsking(null)
      setDetails(null)
      reload()
      reloadProfile()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  const status = data.status ? STATUS[data.status] : undefined
  return (
    <div className="me-stack">
      <Panel title="ADA accommodation">
        <PanelRow>
          <div className="me-form" style={{ width: '100%' }}>
            <label className="me-row" htmlFor="ada-ask">
              <span className="me-row__text">
                <span className="me-row__title">I'd like an accommodation</span>
                <span className="me-row__sub">Staff see your request and arrange what they can.</span>
              </span>
              <input
                id="ada-ask"
                type="checkbox"
                role="switch"
                className="switch"
                checked={requested}
                onChange={(e) => {
                  setAsking(e.target.checked)
                  if (!e.target.checked && data.requested) void save(false)
                }}
              />
            </label>
            {requested && (
              <>
                <label className="field">
                  <span className="field__label">What would help? (optional)</span>
                  <textarea className="input" value={text} maxLength={data.max_length} onChange={(e) => setDetails(e.target.value)} />
                  <span className="field__hint">{privacy(community.name)}</span>
                </label>
                <div>
                  <Button variant="primary" busy={busy} busyLabel="Saving…" onClick={() => void save(true)}>
                    {data.requested ? 'Save changes' : 'Send request'}
                  </Button>
                </div>
              </>
            )}
            {status && data.status !== 'withdrawn' && (
              <div className="me-row">
                <span className="me-row__sub">Staff status:</span>
                <Chip tone={status.tone}>{status.label}</Chip>
              </div>
            )}
            {data.changed_since_arranged && (
              <p className="me-note">Staff have been told about your change. What they arranged stays in place while they check it.</p>
            )}
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

export function EmergencyContact() {
  const { community } = useCommunity()
  const { reload: reloadProfile } = useProfile()
  const { data, reload } = useSection('emergency-contact', () => api.GET('/api/me/emergency-contact').then(unwrap))
  const [form, setForm] = useState<{ name: string; relationship: string; phone: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  if (!data) return <Spinner label="Loading your emergency contact" />
  const value = form ?? { name: data.name ?? '', relationship: data.relationship ?? '', phone: data.phone ?? '' }
  const dirty = form !== null

  async function save() {
    setBusy(true)
    setError(null)
    try {
      await api
        .PUT('/api/me/emergency-contact', { body: { name: value.name, relationship: value.relationship || null, phone: value.phone } })
        .then(unwrap)
      setForm(null)
      setSaved('Saved.')
      reload()
      reloadProfile()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    setBusy(true)
    try {
      done(await api.DELETE('/api/me/emergency-contact'))
      setConfirm(false)
      setForm(null)
      setSaved('Removed.')
      reload()
      reloadProfile()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="me-stack">
      <Panel title="Emergency contact">
        <PanelRow>
          <form
            className="me-form"
            style={{ width: '100%' }}
            onSubmit={(e) => {
              e.preventDefault()
              void save()
            }}
          >
            <p className="me-note">Who should {community.name} staff call if something happens to you at an event?</p>
            <label className="field">
              <span className="field__label">Name</span>
              <input className="input" autoComplete="off" maxLength={100} value={value.name} onChange={(e) => setForm({ ...value, name: e.target.value })} />
            </label>
            <div className="field-grid">
              <label className="field">
                <span className="field__label">Relationship (optional)</span>
                <input
                  className="input"
                  autoComplete="off"
                  maxLength={60}
                  value={value.relationship}
                  onChange={(e) => setForm({ ...value, relationship: e.target.value })}
                />
              </label>
              <label className="field">
                <span className="field__label">Phone</span>
                <input
                  className="input"
                  type="tel"
                  autoComplete="off"
                  maxLength={40}
                  value={value.phone}
                  onChange={(e) => setForm({ ...value, phone: e.target.value })}
                />
              </label>
            </div>
            {data.last_viewed_at && (
              <p className="me-note">
                Staff last opened this {new Date(data.last_viewed_at).toLocaleString()} ({data.last_viewed_by}).
              </p>
            )}
            <div className="me-row">
              {data.name && (
                <Button type="button" variant="ghost" icon="delete" onClick={() => setConfirm(true)}>
                  Remove
                </Button>
              )}
              <span style={{ flex: 1 }} />
              <Button type="submit" variant="primary" disabled={!dirty} busy={busy} busyLabel="Saving…">
                Save
              </Button>
            </div>
          </form>
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
      <FormDialog
        open={confirm}
        onOpenChange={setConfirm}
        size="sm"
        title="Remove your emergency contact?"
        description="It's deleted straight away. Staff won't have anyone to call for you until you add one again."
        primaryLabel="Remove"
        destructive
        busy={busy}
        busyLabel="Removing…"
        onPrimary={() => void remove()}
      >
        {null}
      </FormDialog>
    </div>
  )
}
