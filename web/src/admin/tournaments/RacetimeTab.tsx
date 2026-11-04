import { useEffect, useId, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, unwrap } from '../../api/client'
import { Banner, Button, Panel, PanelRow, Spinner } from '../../components'
import { message } from '../../me/data'
import { MinutesField, SwitchRow } from '../kit'
import { DiscordPostPreview, PickSelect, useRoomOptions } from '../racetime/roomOptions'
import { useAdminQuery, type BoardRow, type Tournament } from './data'
import '../../screens/screens.css'

type Draft = {
  racetime_bot_id: number | null
  race_room_profile_id: number | null
  racetime_default_goal: string
  racetime_group_race: boolean
  racetime_auto_create_rooms: boolean
  room_open_minutes_before: number | null
  require_racetime_link: boolean
  racetime_bot_runs_room: boolean
  racetime_seed_post_minutes: number | null
  racetime_lock_at_seed: boolean
  racetime_announce_channel_id: string | null
  racetime_announce_role_id: string | null
}

function toDraft(t: Tournament): Draft {
  return {
    racetime_bot_id: t.racetime_bot_id ?? null,
    race_room_profile_id: t.race_room_profile_id ?? null,
    racetime_default_goal: t.racetime_default_goal ?? '',
    racetime_group_race: t.racetime_group_race ?? false,
    racetime_auto_create_rooms: t.racetime_auto_create_rooms ?? false,
    room_open_minutes_before: t.room_open_minutes_before ?? 30,
    require_racetime_link: t.require_racetime_link ?? false,
    racetime_bot_runs_room: t.racetime_bot_runs_room ?? false,
    racetime_seed_post_minutes: t.racetime_seed_post_minutes ?? 10,
    racetime_lock_at_seed: t.racetime_lock_at_seed ?? false,
    racetime_announce_channel_id: t.racetime_announce_channel_id ?? null,
    racetime_announce_role_id: t.racetime_announce_role_id ?? null,
  }
}

/** The fields that changed; a blank goal or id is sent as null so the server clears it. */
function changes(before: Draft, after: Draft): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const key of Object.keys(after) as (keyof Draft)[]) {
    if (after[key] === before[key]) continue
    const value = after[key]
    out[key] = value === '' ? null : value
  }
  return out
}

/** How the tournament's racetime rooms open and run: head-to-head matches or group races. */
export function RacetimeTab({ row, base }: { row: BoardRow; base: string }) {
  const tournament = useAdminQuery(`tournament/${row.id}`, () =>
    api.GET('/api/tournaments/{tournament_id}', { params: { path: { tournament_id: row.id } } }).then(unwrap),
  )
  const options = useRoomOptions()
  const original = useMemo(() => (tournament.data ? toDraft(tournament.data) : null), [tournament.data])
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const typeId = useId()

  useEffect(() => setDraft(original), [original])

  if (tournament.error && !tournament.data) {
    return (
      <Banner tone="error" action={<Button variant="outline" onClick={tournament.reload}>Try again</Button>}>
        {tournament.error.message}
      </Banner>
    )
  }
  if (!draft || !original) return <Spinner label="Loading racetime settings" />

  const set = (patch: Partial<Draft>) => {
    setSaved(false)
    setDraft({ ...draft, ...patch })
  }
  const diff = changes(original, draft)
  const dirty = Object.keys(diff).length > 0
  const group = draft.racetime_group_race
  const opts = options.data
  const channelName = opts?.channels.find((c) => c.id === draft.racetime_announce_channel_id)?.name
  const roleName = opts?.roles.find((r) => r.id === draft.racetime_announce_role_id)?.name

  async function save() {
    setBusy(true)
    setError(null)
    try {
      await api.PATCH('/api/tournaments/{tournament_id}', { params: { path: { tournament_id: row.id } }, body: diff }).then(unwrap)
      setSaved(true)
      tournament.reload()
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
      {error && <Banner tone="error">{error}</Banner>}
      {saved && !dirty && <Banner tone="info">Saved.</Banner>}
      {options.error && !opts && <Banner tone="warning">Couldn't load the bots and Discord channels: {options.error.message}</Banner>}
      <Panel title="Room">
        <PanelRow>
          <div className="t-form">
            <div className="t-form__grid">
              <PickSelect
                label="Racetime bot"
                value={draft.racetime_bot_id === null ? null : String(draft.racetime_bot_id)}
                options={opts?.bots}
                none="No bot (no racetime rooms)"
                onChange={(v) => set({ racetime_bot_id: v === null ? null : Number(v) })}
              />
              <PickSelect
                label="Room profile"
                value={draft.race_room_profile_id === null ? null : String(draft.race_room_profile_id)}
                options={opts?.profiles}
                none="None (racetime's defaults)"
                onChange={(v) => set({ race_room_profile_id: v === null ? null : Number(v) })}
              />
              <label className="field">
                <span className="field__label">Goal</span>
                <input
                  className="input"
                  value={draft.racetime_default_goal}
                  placeholder="e.g. Beat the game"
                  onChange={(e) => set({ racetime_default_goal: e.target.value })}
                />
                <span className="field__hint">Used when the profile doesn't set one.</span>
              </label>
            </div>
            <div className="field">
              <span className="field__label" id={typeId}>
                Race type
              </span>
              <div className="segmented" role="radiogroup" aria-labelledby={typeId}>
                {([
                  [false, 'Head-to-head'],
                  [true, 'Group race'],
                ] as const).map(([value, label]) => (
                  <span key={label}>
                    <input
                      type="radio"
                      id={`${typeId}-${value}`}
                      name={typeId}
                      checked={group === value}
                      onChange={() => set(value ? { racetime_group_race: true, racetime_bot_runs_room: true, racetime_auto_create_rooms: true } : { racetime_group_race: false })}
                    />
                    <label htmlFor={`${typeId}-${value}`}>{label}</label>
                  </span>
                ))}
              </div>
              <span className="field__hint">
                {group ? 'A group race has no player list. Anyone can join its room.' : 'Each match gets its own room, open to its players.'}
              </span>
            </div>
          </div>
        </PanelRow>
      </Panel>
      {group ? (
        <Panel title="Group race">
          <PanelRow>
            <div className="t-form">
              {opts?.discord_problem && <Banner tone="warning">{opts.discord_problem} The room still opens; it just isn't posted.</Banner>}
              <div className="t-form__grid">
                <PickSelect
                  label="Announce in"
                  value={draft.racetime_announce_channel_id}
                  options={opts?.channels}
                  none="Don't post in Discord"
                  prefix="#"
                  onChange={(v) => set({ racetime_announce_channel_id: v })}
                />
                <PickSelect
                  label="Ping role"
                  hint="Optional."
                  value={draft.racetime_announce_role_id}
                  options={opts?.roles}
                  none="No ping"
                  prefix="@"
                  onChange={(v) => set({ racetime_announce_role_id: v })}
                />
                <MinutesField
                  label="Room opens"
                  hint="Minutes before the start."
                  min={0}
                  value={draft.room_open_minutes_before}
                  onChange={(n) => set({ room_open_minutes_before: n })}
                />
                <MinutesField
                  label="Seed posts"
                  hint="Minutes before the start."
                  min={0}
                  value={draft.racetime_seed_post_minutes}
                  onChange={(n) => set({ racetime_seed_post_minutes: n })}
                />
              </div>
              <p className="rt-note">
                The seed rolls from the preset on the <Link to={`${base}/settings`}>Settings tab</Link>.
              </p>
              <SwitchRow
                label="Lock the room when the seed posts"
                hint="Nobody can join after the seed is out. Usual for qualifiers, not for dailies."
                checked={draft.racetime_lock_at_seed}
                onChange={(v) => set({ racetime_lock_at_seed: v })}
              />
              <SwitchRow
                label="Bot runs the room"
                hint="Always on for a group race: it greets, posts the seed and records the finish order."
                checked
                disabled
                onChange={() => undefined}
              />
            </div>
          </PanelRow>
        </Panel>
      ) : null}
      {group && (
        <Panel title="Preview of the Discord post">
          <PanelRow>
            <DiscordPostPreview
              title={`${row.name} race`}
              channel={channelName ?? null}
              role={roleName ?? null}
              seedMinutes={draft.racetime_seed_post_minutes ?? 0}
              locks={draft.racetime_lock_at_seed}
            />
          </PanelRow>
        </Panel>
      )}
      {!group && (
        <Panel title="Matches">
          <PanelRow>
            <div className="t-form">
              <SwitchRow
                label="Open rooms on their own"
                hint="Opens each scheduled match's room before it starts, once every player has linked racetime."
                checked={draft.racetime_auto_create_rooms}
                onChange={(v) => set({ racetime_auto_create_rooms: v })}
              />
              <SwitchRow
                label="Players must link racetime"
                hint="Players can't sign up or book until they've linked a racetime account."
                checked={draft.require_racetime_link}
                onChange={(v) => set({ require_racetime_link: v })}
              />
              <SwitchRow
                label="Bot runs the room"
                hint="It greets, posts the seed, takes !seed and !fpa, and holds a result staff should check."
                checked={draft.racetime_bot_runs_room}
                onChange={(v) => set({ racetime_bot_runs_room: v })}
              />
              <div className="t-form__grid">
                <MinutesField
                  label="Room opens"
                  hint="Minutes before the match."
                  min={0}
                  value={draft.room_open_minutes_before}
                  onChange={(n) => set({ room_open_minutes_before: n })}
                />
                <MinutesField
                  label="Seed posts"
                  hint="Minutes before the match, when the bot runs the room."
                  min={0}
                  value={draft.racetime_seed_post_minutes}
                  onChange={(n) => set({ racetime_seed_post_minutes: n })}
                />
              </div>
            </div>
          </PanelRow>
        </Panel>
      )}
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
