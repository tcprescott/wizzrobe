import { useEffect, useState } from 'react'
import { Route, Routes, useParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../../api/client'
import { Banner, Button, Chip, EmptyState, FormDialog, PageHeader, RowMenu, Spinner, type MenuItem, type Tone } from '../../components'
import { effectiveZone, formatDayTime, formatTime, wallClock, zonedInstant } from '../../lib/time'
import { Detail, DetailEmpty, DetailLine, DetailSection, SideListCard, SideListLayout, SideListRow } from '../kit/SideList'
import { useAdmin } from '../kit/useAdminNav'
import { useAction, useAdminData } from '../peopleKit'

type Meetup = Schemas['MeetupResponse']
type Filter = 'pending' | 'approved' | 'closed' | 'all'

const STATUS: Record<string, { label: string; tone: Tone }> = {
  pending: { label: 'Waiting', tone: 'pending' },
  approved: { label: 'Listed', tone: 'ok' },
  rejected: { label: 'Rejected', tone: 'cancelled' },
  cancelled: { label: 'Cancelled', tone: 'neutral' },
}

function inFilter(m: Meetup, f: Filter): boolean {
  if (f === 'all') return true
  if (f === 'closed') return m.status === 'rejected' || m.status === 'cancelled'
  return m.status === f
}

function endsAt(m: Meetup): string {
  return new Date(new Date(m.starts_at).getTime() + m.duration_minutes * 60000).toISOString()
}

function StatusChip({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status, tone: 'neutral' as Tone }
  return <Chip tone={s.tone}>{s.label}</Chip>
}

function EditDialog({ open, onOpenChange, meetup, zone, onSaved }: { open: boolean; onOpenChange: (o: boolean) => void; meetup: Meetup; zone: string; onSaved: () => void }) {
  const [form, setForm] = useState(() => ({ ...meetup, ...wallClock(meetup.starts_at, zone), capacity: meetup.rsvp_capacity?.toString() ?? '' }))
  const { busy, error, run, setError } = useAction()
  useEffect(() => {
    if (open) {
      setForm({ ...meetup, ...wallClock(meetup.starts_at, zone), capacity: meetup.rsvp_capacity?.toString() ?? '' })
      setError(null)
    }
  }, [open, meetup, zone, setError])
  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => setForm((f) => ({ ...f, [key]: value }))
  async function save() {
    const ok = await run('save', () =>
      api
        .PUT('/api/admin/meetups/{meetup_id}', {
          params: { path: { meetup_id: meetup.id } },
          body: {
            title: form.title.trim(),
            description: form.description,
            location: form.location?.trim() || null,
            starts_at: zonedInstant(form.date, form.time, zone).toISOString(),
            duration_minutes: form.duration_minutes,
            rsvp_enabled: form.rsvp_enabled,
            rsvp_capacity: form.rsvp_enabled && form.capacity ? Number(form.capacity) : null,
          },
        })
        .then(unwrap),
    )
    if (ok) {
      onOpenChange(false)
      onSaved()
    }
  }
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title="Edit meetup" primaryLabel="Save" busy={busy === 'save'} onPrimary={() => void save()}>
      <label className="field">
        <span className="field__label">Title</span>
        <input className="input" value={form.title} maxLength={120} onChange={(e) => set('title', e.target.value)} />
      </label>
      <div className="admin-fields">
        <label className="field">
          <span className="field__label">Date</span>
          <input className="input" type="date" value={form.date} onChange={(e) => set('date', e.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">Starts</span>
          <input className="input" type="time" value={form.time} onChange={(e) => set('time', e.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">Minutes</span>
          <input className="input" type="number" min={15} step={15} value={form.duration_minutes} onChange={(e) => set('duration_minutes', Number(e.target.value))} />
        </label>
      </div>
      <label className="field">
        <span className="field__label">Where</span>
        <input className="input" value={form.location ?? ''} maxLength={200} onChange={(e) => set('location', e.target.value)} />
      </label>
      <label className="field">
        <span className="field__label">Description</span>
        <textarea className="input" rows={5} value={form.description} onChange={(e) => set('description', e.target.value)} />
        <span className="field__hint">Markdown: formatting and links only.</span>
      </label>
      <label className="admin-switch">
          <span>Take RSVPs</span>
          <input type="checkbox" role="switch" className="switch" checked={form.rsvp_enabled} onChange={(e) => set('rsvp_enabled', e.target.checked)} />
        </label>
      {form.rsvp_enabled && (
        <label className="field">
          <span className="field__label">Capacity</span>
          <input className="input" type="number" min={1} value={form.capacity} onChange={(e) => set('capacity', e.target.value)} />
          <span className="field__hint">Leave empty for no limit.</span>
        </label>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </FormDialog>
  )
}

function MeetupDetail({ id, zone, onChange }: { id: number; zone: string; onChange: () => void }) {
  const { data, error, reload } = useAdminData(`meetup/${id}`, () =>
    api.GET('/api/admin/meetups/{meetup_id}', { params: { path: { meetup_id: id } } }).then(unwrap),
  )
  const attendees = useAdminData(`meetup/${id}/attendees`, () =>
    api.GET('/api/admin/meetups/{meetup_id}/attendees', { params: { path: { meetup_id: id } } }).then(unwrap),
  )
  const [reason, setReason] = useState('')
  const [dialog, setDialog] = useState<'edit' | 'cancel' | 'pull' | null>(null)
  const { busy, error: actionError, run, setError } = useAction()

  if (error && !data) return <Banner tone="error">{error.message}</Banner>
  if (!data) return <Spinner label="Loading the meetup" />
  const path = { params: { path: { meetup_id: id } } }
  const changed = () => {
    reload()
    onChange()
  }
  async function decide(kind: 'approve' | 'reject' | 'cancel') {
    const ok = await run(kind, () =>
      kind === 'approve'
        ? api.POST('/api/admin/meetups/{meetup_id}/approve', path).then(unwrap)
        : kind === 'cancel'
          ? api.POST('/api/admin/meetups/{meetup_id}/cancel', path).then(unwrap)
          : api.POST('/api/admin/meetups/{meetup_id}/reject', { ...path, body: { reason: reason.trim() } }).then(unwrap),
    )
    if (ok) {
      setReason('')
      setDialog(null)
      changed()
    }
  }

  const pending = data.status === 'pending'
  const open = pending || data.status === 'approved'
  const menu: MenuItem[] = [{ label: 'Edit…', icon: 'edit', onSelect: () => setDialog('edit') }]
  if (data.status === 'approved') menu.push({ label: 'Pull from the list…', icon: 'block', onSelect: () => { setError(null); setDialog('pull') } })
  if (open) menu.push({ label: 'Cancel meetup…', icon: 'event_busy', destructive: true, onSelect: () => { setError(null); setDialog('cancel') } })

  return (
    <Detail
      title={data.title}
      sub={<StatusChip status={data.status} />}
      actions={<RowMenu label={`More for ${data.title}`} items={menu} />}
      footer={
        pending && (
          <>
            <Button variant="outline" busy={busy === 'reject'} disabled={!reason.trim()} onClick={() => void decide('reject')}>
              Reject
            </Button>
            <Button variant="primary" icon="check" busy={busy === 'approve'} onClick={() => void decide('approve')}>
              Approve
            </Button>
          </>
        )
      }
    >
      {actionError && !dialog && <Banner tone="error">{actionError}</Banner>}
      <DetailSection>
        <DetailLine icon="schedule">
          {formatDayTime(data.starts_at, zone)} to {formatTime(endsAt(data), zone)}
        </DetailLine>
        {data.location && <DetailLine icon="location_on">{data.location}</DetailLine>}
        <DetailLine icon="person">Hosted by {data.host_name}</DetailLine>
        {data.description && <p className="prewrap">{data.description}</p>}
      </DetailSection>
      {data.review_note && data.status === 'rejected' && (
        <DetailSection title="Why it was rejected">
          <p className="prewrap">{data.review_note}</p>
        </DetailSection>
      )}
      {data.rsvp_enabled && (
        <DetailSection title={`Going${attendees.data ? ` (${attendees.data.length}${data.rsvp_capacity ? ` of ${data.rsvp_capacity}` : ''})` : ''}`}>
          {!attendees.data ? (
            <Spinner label="Loading who's going" />
          ) : attendees.data.length === 0 ? (
            <p className="admin-muted">No one yet.</p>
          ) : (
            <p>{attendees.data.map((a) => a.name).join(', ')}</p>
          )}
        </DetailSection>
      )}
      {pending && (
        <DetailSection>
          <label className="field">
            <span className="field__label">Reason (sent to the host if you reject it)</span>
            <textarea className="input" rows={3} value={reason} placeholder="What needs to change before it can be listed?" onChange={(e) => setReason(e.target.value)} />
          </label>
        </DetailSection>
      )}

      <EditDialog open={dialog === 'edit'} onOpenChange={(o) => setDialog(o ? 'edit' : null)} meetup={data} zone={zone} onSaved={changed} />
      <FormDialog
        open={dialog === 'pull'}
        onOpenChange={(o) => setDialog(o ? 'pull' : null)}
        title="Pull it from the list?"
        size="sm"
        primaryLabel="Pull it"
        destructive
        busy={busy === 'reject'}
        onPrimary={() => void decide('reject')}
      >
        <label className="field">
          <span className="field__label">Reason (sent to the host)</span>
          <textarea className="input" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        {actionError && (
          <p className="form-error" role="alert">
            {actionError}
          </p>
        )}
      </FormDialog>
      <FormDialog
        open={dialog === 'cancel'}
        onOpenChange={(o) => setDialog(o ? 'cancel' : null)}
        title={`Cancel ${data.title}?`}
        size="sm"
        primaryLabel="Cancel meetup"
        cancelLabel="Keep it"
        destructive
        busy={busy === 'cancel'}
        onPrimary={() => void decide('cancel')}
      >
        <p>It comes off the list, and people who said they're going hear it's off.</p>
        {actionError && (
          <p className="form-error" role="alert">
            {actionError}
          </p>
        )}
      </FormDialog>
    </Detail>
  )
}

function MeetupsPage({ board, reload, base }: { board: Schemas['MeetupsBoardResponse']; reload: () => void; base: string }) {
  const { meetupId } = useParams()
  const id = meetupId ? Number(meetupId) : null
  const zone = effectiveZone(board.zone)
  const waiting = board.meetups.filter((m) => m.status === 'pending').length
  const [filter, setFilter] = useState<Filter>(waiting ? 'pending' : 'approved')
  const shown = board.meetups.filter((m) => inFilter(m, filter))
  const count = (f: Filter) => board.meetups.filter((m) => inFilter(m, f)).length
  const pills: [Filter, string][] = [
    ['pending', `Waiting ${waiting}`],
    ['approved', `Listed ${count('approved')}`],
    ['closed', `Rejected or cancelled ${count('closed')}`],
    ['all', `All ${board.meetups.length}`],
  ]
  return (
    <>
      <PageHeader title="Meetups" lede="Meetups attendees want to run. Approve one to list it, or reject it with a reason the host sees." />
      <SideListLayout
        open={id !== null}
        backTo={base}
        backLabel="Meetups"
        list={
          <div className="admin-stack">
            <div className="admin-pills" role="group" aria-label="Show">
              {pills.map(([key, label]) => (
                <button key={key} type="button" className="admin-pill" aria-pressed={filter === key} onClick={() => setFilter(key)}>
                  {label}
                </button>
              ))}
            </div>
            {shown.length === 0 ? (
              <EmptyState icon="celebration" title={filter === 'pending' ? 'Nothing waiting on you' : 'No meetups here'} />
            ) : (
              <SideListCard label="Meetups">
                {shown.map((m) => (
                  <SideListRow
                    key={m.id}
                    to={`${base}/${m.id}`}
                    title={m.title}
                    sub={`${formatDayTime(m.starts_at, zone)} · ${m.host_name}`}
                    end={<StatusChip status={m.status} />}
                  />
                ))}
              </SideListCard>
            )}
          </div>
        }
        detail={id !== null ? <MeetupDetail key={id} id={id} zone={zone} onChange={reload} /> : <DetailEmpty>Pick a meetup to review it.</DetailEmpty>}
      />
    </>
  )
}

/** Admin → Meetups: the review queue beside the open meetup. */
export function MeetupsScreen() {
  const { adminBase } = useAdmin()
  const base = `${adminBase}/meetups`
  const { data, error, reload } = useAdminData('meetups', () => api.GET('/api/admin/meetups').then(unwrap))
  if (error && !data) {
    return (
      <>
        <PageHeader title="Meetups" />
        <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
          {error.message}
        </Banner>
      </>
    )
  }
  if (!data) return <Spinner label="Loading meetups" />
  return (
    <Routes>
      <Route path=":meetupId" element={<MeetupsPage board={data} reload={reload} base={base} />} />
      <Route path="*" element={<MeetupsPage board={data} reload={reload} base={base} />} />
    </Routes>
  )
}
