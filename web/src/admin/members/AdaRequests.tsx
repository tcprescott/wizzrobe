import { useEffect, useState } from 'react'
import { api, unwrap, type Schemas } from '../../api/client'
import { Banner, Button, Chip, EmptyState, Spinner } from '../../components'
import { effectiveZone, formatDay, formatDayTime } from '../../lib/time'
import { Detail, DetailSection, SideListCard, SideListRow } from '../kit/SideList'
import { useAction, useAdminData } from '../peopleKit'

type Status = Schemas['AccommodationStatus']

const STATUSES: { value: Status; label: string }[] = [
  { value: 'new', label: 'New' },
  { value: 'acknowledged', label: 'Acknowledged' },
  { value: 'arranged', label: 'Arranged' },
]

function tone(r: Schemas['AdaRequestResponse']) {
  if (r.status === 'withdrawn') return 'cancelled' as const
  if (r.status === 'arranged' && !r.changed_since_arranged) return 'ok' as const
  return 'pending' as const
}

export function AdaList({ base, zone }: { base: string; zone: string | null | undefined }) {
  const [withdrawn, setWithdrawn] = useState(false)
  const { data, error } = useAdminData(`ada@${withdrawn}`, () =>
    api.GET('/api/admin/members/ada-requests', { params: { query: { include_withdrawn: withdrawn } } }).then(unwrap),
  )
  const z = effectiveZone(zone)
  return (
    <div className="admin-stack">
      <div className="admin-toolbar">
        <label className="switch">
          <input type="checkbox" checked={withdrawn} onChange={(e) => setWithdrawn(e.target.checked)} />
          <span>Show withdrawn</span>
        </label>
      </div>
      {error && !data ? (
        <Banner tone="error">{error.message}</Banner>
      ) : !data ? (
        <Spinner label="Loading ADA requests" />
      ) : data.length === 0 ? (
        <EmptyState icon="accessible" title="No ADA requests" />
      ) : (
        <SideListCard label="ADA requests">
          {data.map((r) => (
            <SideListRow
              key={r.id}
              to={`${base}/${r.id}`}
              title={r.preferred_name}
              sub={`Sent ${formatDay(r.created_at, z)}${r.changed_since_arranged ? ' · changed since arranged' : ''}`}
              end={<Chip tone={tone(r)}>{r.label}</Chip>}
            />
          ))}
        </SideListCard>
      )}
    </div>
  )
}

export function AdaRequestDetail({ requestId, zone, onChange }: { requestId: number; zone: string | null | undefined; onChange: () => void }) {
  const { data, error, reload } = useAdminData(`ada-request/${requestId}`, () =>
    api.GET('/api/admin/members/ada-requests/{request_id}', { params: { path: { request_id: requestId } } }).then(unwrap),
  )
  const [status, setStatus] = useState<Status>('new')
  const [notes, setNotes] = useState('')
  const { busy, error: saveError, run } = useAction()
  const z = effectiveZone(zone)
  useEffect(() => {
    if (data) {
      setStatus(data.status as Status)
      setNotes(data.staff_notes ?? '')
    }
  }, [data])

  if (error && !data) return <Banner tone="error">{error.message}</Banner>
  if (!data) return <Spinner label="Loading the request" />
  const withdrawn = data.status === 'withdrawn'
  const dirty = status !== data.status || notes !== (data.staff_notes ?? '') || data.changed_since_arranged

  async function save() {
    const ok = await run('save', () =>
      api
        .PUT('/api/admin/members/ada-requests/{request_id}', {
          params: { path: { request_id: requestId } },
          body: { status, staff_notes: notes.trim() || null },
        })
        .then(unwrap),
    )
    if (ok) {
      reload()
      onChange()
    }
  }

  return (
    <Detail
      title={data.preferred_name}
      sub={`@${data.username} · sent ${formatDayTime(data.created_at, z)} · updated ${formatDayTime(data.updated_at, z)}`}
      actions={<Chip tone={tone(data)}>{data.label}</Chip>}
      footer={
        !withdrawn && (
          <Button variant="primary" busy={busy === 'save'} disabled={!dirty} onClick={() => void save()}>
            {data.changed_since_arranged ? 'Save and mark reviewed' : 'Save'}
          </Button>
        )
      }
    >
      {data.changed_since_arranged && (
        <Banner tone="warning">They changed this after it was arranged. Check it still works, then save to mark it reviewed.</Banner>
      )}
      <DetailSection title="What they asked for">
        <p className="prewrap">{data.details || 'Nothing written.'}</p>
        {data.arranged_details && (
          <p className="admin-muted prewrap">What was arranged against: {data.arranged_details}</p>
        )}
      </DetailSection>
      {withdrawn ? (
        <DetailSection title="Staff notes">
          <p className="prewrap">{data.staff_notes || 'None.'}</p>
          <p className="admin-muted">They withdrew it, so it can't be changed.</p>
        </DetailSection>
      ) : (
        <DetailSection title="Where it stands">
          <label className="field">
            <span className="field__label">Status</span>
            <select className="input" value={status} onChange={(e) => setStatus(e.target.value as Status)}>
              {STATUSES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field__label">Staff notes</span>
            <textarea className="input" rows={4} value={notes} onChange={(e) => setNotes(e.target.value)} />
            <span className="field__hint">Only staff see these.</span>
          </label>
          {saveError && (
            <p className="form-error" role="alert">
              {saveError}
            </p>
          )}
        </DetailSection>
      )}
    </Detail>
  )
}
