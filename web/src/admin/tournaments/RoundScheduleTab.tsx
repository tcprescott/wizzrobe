import { useEffect, useState } from 'react'
import { api, unwrap } from '../../api/client'
import { Banner, Button, Chip, EmptyState, Panel, PanelRow, Spinner } from '../../components'
import { done, message } from '../../me/data'
import { useAdminQuery, type BoardRow, type RoundStage } from './data'
import { InstantField } from './fields'

type Entry = {
  opens_at: string | null
  closes_at: string | null
  daily_from: string
  daily_until: string
  best_of: 1 | 3 | 5
}

function toEntry(s: RoundStage): Entry {
  return {
    opens_at: s.opens_at ?? null,
    closes_at: s.closes_at ?? null,
    daily_from: s.daily_from ?? '',
    daily_until: s.daily_until ?? '',
    best_of: ([1, 3, 5].includes(s.best_of) ? s.best_of : 1) as 1 | 3 | 5,
  }
}

/** Each bracket stage's booking window, daily hours and games per matchup. */
export function RoundScheduleTab({ row, zone }: { row: BoardRow; zone: string | null }) {
  const stages = useAdminQuery(`tournaments/${row.id}/round-schedule`, () =>
    api.GET('/api/tournaments/{tournament_id}/round-schedule', { params: { path: { tournament_id: row.id } } }).then(unwrap),
  )
  const [draft, setDraft] = useState<Record<string, Entry>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const original = (stages.data ?? []).reduce<Record<string, Entry>>((acc, s) => ({ ...acc, [s.key]: toEntry(s) }), {})
  useEffect(() => {
    if (stages.data) setDraft(Object.fromEntries(stages.data.map((s) => [s.key, toEntry(s)])))
  }, [stages.data])

  if (stages.error && !stages.data) {
    return (
      <Banner tone="error" action={<Button variant="outline" onClick={stages.reload}>Try again</Button>}>
        {stages.error.message}
      </Banner>
    )
  }
  if (!stages.data) return <Spinner label="Loading round schedule" />
  if (stages.data.length === 0) {
    return (
      <EmptyState icon="event_note" title="No stages yet">
        Sync the bracket and its stages show up here.
      </EmptyState>
    )
  }

  const dirty = JSON.stringify(draft) !== JSON.stringify(original)
  const set = (key: string, patch: Partial<Entry>) => {
    setSaved(false)
    setDraft({ ...draft, [key]: { ...draft[key], ...patch } as Entry })
  }

  async function save() {
    setBusy(true)
    setError(null)
    try {
      const body = Object.fromEntries(
        Object.entries(draft).map(([k, e]) => [k, { ...e, daily_from: e.daily_from || null, daily_until: e.daily_until || null }]),
      )
      done(
        await api.PUT('/api/admin/tournaments/{tournament_id}/round-schedule', {
          params: { path: { tournament_id: row.id } },
          body: { stages: body },
        }),
      )
      setSaved(true)
      stages.reload()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form
      className="t-form"
      onSubmit={(e) => {
        e.preventDefault()
        void save()
      }}
    >
      <p className="t-row__sub" style={{ margin: 0 }}>
        Players can only book a stage's matches inside its window and daily hours. Staff can still book anyway.
      </p>
      {error && <Banner tone="error">{error}</Banner>}
      {saved && !dirty && <Banner tone="info">Saved.</Banner>}
      {stages.data.map((s) => {
        const e = draft[s.key]
        if (!e) return null
        return (
          <Panel
            key={s.key}
            title={s.label}
            actions={s.placeholder ? <Chip>Not drawn yet</Chip> : <Chip tone={s.booked === s.total ? 'ok' : 'neutral'}>{s.detail}</Chip>}
          >
            <PanelRow>
              <div className="t-form__grid">
                <InstantField label="Opens" value={e.opens_at} zone={zone} onChange={(v) => set(s.key, { opens_at: v })} />
                <InstantField label="Closes" value={e.closes_at} zone={zone} onChange={(v) => set(s.key, { closes_at: v })} />
                <label className="field">
                  <span className="field__label">Daily from</span>
                  <input className="input" type="time" value={e.daily_from} onChange={(ev) => set(s.key, { daily_from: ev.target.value })} />
                </label>
                <label className="field">
                  <span className="field__label">Daily until</span>
                  <input className="input" type="time" value={e.daily_until} onChange={(ev) => set(s.key, { daily_until: ev.target.value })} />
                </label>
                <label className="field">
                  <span className="field__label">Games per matchup</span>
                  <select className="input" value={e.best_of} onChange={(ev) => set(s.key, { best_of: Number(ev.target.value) as 1 | 3 | 5 })}>
                    <option value={1}>Best of 1</option>
                    <option value={3}>Best of 3</option>
                    <option value={5}>Best of 5</option>
                  </select>
                </label>
              </div>
            </PanelRow>
          </Panel>
        )
      })}
      <div className="t-actions">
        <Button variant="ghost" disabled={!dirty || busy} onClick={() => setDraft(original)}>
          Undo changes
        </Button>
        <Button type="submit" disabled={!dirty} busy={busy} busyLabel="Saving…">
          Save
        </Button>
      </div>
    </form>
  )
}
