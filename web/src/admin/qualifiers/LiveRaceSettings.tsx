import { useEffect, useMemo, useState } from 'react'
import { api, unwrap, type Schemas } from '../../api/client'
import { Banner, Button, Panel, PanelRow, Spinner } from '../../components'
import { message } from '../../me/data'
import { MinutesField, SwitchRow, useAdminQuery } from '../kit'
import { PickSelect, useRoomOptions } from '../racetime/roomOptions'

type Qualifier = Schemas['AsyncQualifierResponse']

type Draft = {
  live_race_bot_id: number | null
  live_race_room_open_minutes: number | null
  live_race_seed_post_minutes: number | null
  live_race_lock_at_seed: boolean
  live_race_announce_channel_id: string | null
  live_race_announce_role_id: string | null
}

const toDraft = (q: Qualifier): Draft => ({
  live_race_bot_id: q.live_race_bot_id ?? null,
  live_race_room_open_minutes: q.live_race_room_open_minutes ?? 60,
  live_race_seed_post_minutes: q.live_race_seed_post_minutes ?? 10,
  live_race_lock_at_seed: q.live_race_lock_at_seed ?? true,
  live_race_announce_channel_id: q.live_race_announce_channel_id ?? null,
  live_race_announce_role_id: q.live_race_announce_role_id ?? null,
})

/** How a live race with a start time runs: when its room opens, where it's posted, the seed and the lock. */
export function LiveRaceSettings({ qualifierId }: { qualifierId: number }) {
  const qualifier = useAdminQuery(`async-qualifier/${qualifierId}`, () =>
    api.GET('/api/async-qualifiers/{qualifier_id}', { params: { path: { qualifier_id: qualifierId } } }).then(unwrap),
  )
  const options = useRoomOptions()
  const original = useMemo(() => (qualifier.data ? toDraft(qualifier.data) : null), [qualifier.data])
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)

  useEffect(() => setDraft(original), [original])

  if (qualifier.error && !qualifier.data) return <Banner tone="error">{qualifier.error.message}</Banner>
  if (!draft || !original) return <Spinner label="Loading live race settings" />

  const set = (patch: Partial<Draft>) => {
    setNote(null)
    setDraft({ ...draft, ...patch })
  }
  const dirty = JSON.stringify(draft) !== JSON.stringify(original)
  const opts = options.data

  async function save() {
    setBusy(true)
    setNote(null)
    try {
      await api
        .PATCH('/api/async-qualifiers/{qualifier_id}', { params: { path: { qualifier_id: qualifierId } }, body: { ...draft!, clear_window: false } })
        .then(unwrap)
      setNote({ tone: 'info', text: 'Saved.' })
      qualifier.reload()
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel title="Live races with a start time">
      <PanelRow>
        <div className="t-form">
          <p className="rt-note">
            The bot opens the room on its own, open to anyone, posts it in Discord, then posts the race's seed before the start.
          </p>
          {note && <Banner tone={note.tone}>{note.text}</Banner>}
          {opts?.discord_problem && <Banner tone="warning">{opts.discord_problem} Rooms still open; they just aren't posted.</Banner>}
          <div className="t-form__grid">
            <PickSelect
              label="Racetime bot"
              value={draft.live_race_bot_id === null ? null : String(draft.live_race_bot_id)}
              options={opts?.bots}
              none="The community's first bot"
              onChange={(v) => set({ live_race_bot_id: v === null ? null : Number(v) })}
            />
            <PickSelect
              label="Announce in"
              value={draft.live_race_announce_channel_id}
              options={opts?.channels}
              none="Don't post in Discord"
              prefix="#"
              onChange={(v) => set({ live_race_announce_channel_id: v })}
            />
            <PickSelect
              label="Ping role"
              hint="Optional."
              value={draft.live_race_announce_role_id}
              options={opts?.roles}
              none="No ping"
              prefix="@"
              onChange={(v) => set({ live_race_announce_role_id: v })}
            />
            <MinutesField
              label="Room opens"
              hint="Minutes before the start."
              min={0}
              value={draft.live_race_room_open_minutes}
              onChange={(n) => set({ live_race_room_open_minutes: n })}
            />
            <MinutesField
              label="Seed posts"
              hint="Minutes before the start."
              min={0}
              value={draft.live_race_seed_post_minutes}
              onChange={(n) => set({ live_race_seed_post_minutes: n })}
            />
          </div>
          <SwitchRow
            label="Lock the room when the seed posts"
            hint="Nobody can join after the seed is out."
            checked={draft.live_race_lock_at_seed}
            onChange={(v) => set({ live_race_lock_at_seed: v })}
          />
          <div className="t-actions">
            <Button variant="outline" disabled={!dirty || busy} onClick={() => setDraft(original)}>
              Undo changes
            </Button>
            <Button disabled={!dirty} onClick={() => void save()} busy={busy} busyLabel="Saving…">
              Save
            </Button>
          </div>
        </div>
      </PanelRow>
    </Panel>
  )
}
