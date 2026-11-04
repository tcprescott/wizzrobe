import { useMemo, useState, type ReactNode } from 'react'
import { Route, Routes, useNavigate, useParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../../api/client'
import {
  ActionsMenu,
  Banner,
  Button,
  Chip,
  ConfirmDialog,
  EmptyState,
  FormDialog,
  Icon,
  PageHeader,
  Spinner,
  type IconName,
  type MenuItem,
  type Tone,
} from '../../components'
import { dayKey, dayLabel, effectiveZone, formatDay, formatDayTime, formatTime } from '../../lib/time'
import { usePhone } from '../../lib/usePhone'
import { message } from '../../me/data'
import { Detail, DetailEmpty, DetailSection, InstantField, SideListCard, SideListLayout, SideListRow, useAdmin, useAdminQuery } from '../kit'
import { DiscordPostPreview, useRoomOptions } from '../racetime/roomOptions'
import './group-races.css'

type Race = Schemas['GroupRaceResponse']
type RaceDetail = Schemas['GroupRaceDetailResponse']
type Kind = Race['kind']

const PATH: Record<Kind, string> = { match: 'matches', qualifier: 'live-races' }
const KIND_OF: Record<string, Kind> = { matches: 'match', 'live-races': 'qualifier' }

/** Admin → Group races: dailies, weeklies and qualifier live races beside the open race. */
export function GroupRacesScreen() {
  return (
    <Routes>
      <Route path=":kind/:id" element={<Board />} />
      <Route path="*" element={<Board />} />
    </Routes>
  )
}

function Board() {
  const { kind: kindPath, id } = useParams()
  const { adminBase } = useAdmin()
  const navigate = useNavigate()
  const base = `${adminBase}/group-races`
  const [finished, setFinished] = useState(false)
  const [adding, setAdding] = useState(false)
  const board = useAdminQuery(`group-races?finished=${finished}`, () =>
    api.GET('/api/group-races', { params: { query: { finished } } }).then(unwrap),
  )
  const kind = kindPath ? KIND_OF[kindPath] : undefined
  const openId = kind && id ? Number(id) : null
  const zone = effectiveZone(board.data?.zone)

  const header = (
    <PageHeader
      title="Group races"
      lede="Dailies, weeklies and qualifier live races. The bot opens each room and posts it in Discord."
      actions={
        <Button icon="add" onClick={() => setAdding(true)}>
          New group race
        </Button>
      }
    />
  )
  if (board.error && !board.data) {
    return (
      <>
        {header}
        <Banner tone="error" action={<Button variant="outline" onClick={board.reload}>Try again</Button>}>
          {board.error.message}
        </Banner>
      </>
    )
  }
  if (!board.data) return <Spinner label="Loading group races" />
  const races = board.data.races

  return (
    <>
      {header}
      <SideListLayout
        open={openId !== null}
        backTo={base}
        backLabel="Group races"
        list={
          <div className="admin-stack">
            <div className="admin-pills" role="group" aria-label="Show">
              <button type="button" className="admin-pill" aria-pressed={!finished} onClick={() => setFinished(false)}>
                Coming up
              </button>
              <button type="button" className="admin-pill" aria-pressed={finished} onClick={() => setFinished(true)}>
                Finished
              </button>
            </div>
            {races.length === 0 ? (
              <EmptyState icon="directions_run" title={finished ? 'No finished races in the last two weeks' : 'No group races coming up'}>
                {finished
                  ? undefined
                  : "Races come from the SpeedGaming schedule for a group race tournament, or add one with New group race."}
              </EmptyState>
            ) : (
              <RaceList races={races} zone={zone} base={base} />
            )}
          </div>
        }
        detail={
          openId !== null && kind ? (
            <RacePane
              key={`${kind}-${openId}`}
              kind={kind}
              id={openId}
              zone={zone}
              onChange={board.reload}
              onCancelled={() => {
                board.reload()
                navigate(base)
              }}
            />
          ) : (
            <DetailEmpty>Pick a race to see what the bot has done and who's in the room.</DetailEmpty>
          )
        }
      />
      {adding && (
        <NewRace
          zone={board.data.zone ?? null}
          onClose={() => setAdding(false)}
          onAdded={(race) => {
            setFinished(false)
            board.reload()
            navigate(`${base}/${PATH[race.kind]}/${race.id}`)
          }}
        />
      )}
    </>
  )
}

function RaceList({ races, zone, base }: { races: Race[]; zone: string; base: string }) {
  const days = useMemo(() => {
    const out: { key: string; label: string; races: Race[] }[] = []
    for (const race of races) {
      const at = race.scheduled_at
      const key = at ? dayKey(at, zone) : 'unscheduled'
      let day = out.find((d) => d.key === key)
      if (!day) {
        const label = at ? dayHeading(at, zone) : 'No start time'
        day = { key, label, races: [] }
        out.push(day)
      }
      day.races.push(race)
    }
    return out
  }, [races, zone])
  return (
    <>
      {days.map((day) => (
        <SideListCard key={day.key} label={day.label} title={day.label}>
          {day.races.map((race) => (
            <SideListRow
              key={`${race.kind}-${race.id}`}
              to={`${base}/${PATH[race.kind]}/${race.id}`}
              leading={<span className="gr-time">{race.scheduled_at ? formatTime(race.scheduled_at, zone) : '—'}</span>}
              title={race.title}
              sub={
                <span className="gr-sub">
                  <StateLine race={race} zone={zone} />
                  <SeriesChip race={race} />
                </span>
              }
            />
          ))}
        </SideListCard>
      ))}
    </>
  )
}

function dayHeading(iso: string, zone: string): string {
  const label = dayLabel(iso, zone)
  const day = formatDay(iso, zone)
  return label === day ? day : `${label} · ${day}`
}

const STATE: Record<Race['state'], { icon: IconName; tone: string }> = {
  scheduled: { icon: 'schedule', tone: 'muted' },
  open: { icon: 'podcasts', tone: 'live' },
  running: { icon: 'directions_run', tone: 'live' },
  finished: { icon: 'check_circle', tone: 'ok' },
}

function stateText(race: Race, zone: string): string {
  switch (race.state) {
    case 'scheduled':
      return race.opens_at ? `Opens ${sameDay(race.opens_at, race.scheduled_at, zone) ? formatTime(race.opens_at, zone) : formatDayTime(race.opens_at, zone)}` : 'Not scheduled'
    case 'open':
      return race.locked_at ? 'Room open · locked' : race.seed_posted_at ? 'Room open · seed out' : 'Room open'
    case 'running':
      return 'Racing'
    case 'finished':
      return `Finished · ${race.finishers} finisher${race.finishers === 1 ? '' : 's'}`
  }
}

function sameDay(a: string, b: string | null | undefined, zone: string): boolean {
  return !!b && dayKey(a, zone) === dayKey(b, zone)
}

function StateLine({ race, zone }: { race: Race; zone: string }) {
  const s = STATE[race.state]
  return (
    <span className={`gr-state gr-state--${s.tone}`}>
      <Icon name={s.icon} size={16} />
      {stateText(race, zone)}
    </span>
  )
}

function SeriesChip({ race }: { race: Race }) {
  const tone: Tone = race.kind === 'qualifier' ? 'info' : 'ok'
  return <Chip tone={tone}>{race.kind === 'qualifier' ? 'Qualifier' : race.series}</Chip>
}

function RacePane({
  kind,
  id,
  zone,
  onChange,
  onCancelled,
}: {
  kind: Kind
  id: number
  zone: string
  onChange: () => void
  onCancelled: () => void
}) {
  const phone = usePhone()
  const load = () =>
    kind === 'match'
      ? api.GET('/api/group-races/matches/{match_id}', { params: { path: { match_id: id } } }).then(unwrap)
      : api.GET('/api/group-races/live-races/{live_race_id}', { params: { path: { live_race_id: id } } }).then(unwrap)
  const race = useAdminQuery(`group-race/${kind}/${id}`, load)
  const options = useRoomOptions()
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)
  const [cancelling, setCancelling] = useState(false)

  if (race.error && !race.data) {
    return (
      <EmptyState icon="info" title="That race isn't here">
        It may have been cancelled. {race.error.message}
      </EmptyState>
    )
  }
  if (!race.data) return <Spinner label="Loading the race" />
  const r = race.data
  const channel = options.data?.channels.find((c) => c.id === r.channel_id)?.name ?? (r.channel_id ? r.channel_id : null)
  const role = options.data?.roles.find((c) => c.id === r.role_id)?.name ?? null

  async function act(action: 'open' | 'seed' | 'announce' | 'lock', done: string) {
    setBusy(action)
    setNote(null)
    try {
      if (kind === 'match') {
        const path = { params: { path: { match_id: id } } }
        if (action === 'open') await api.POST('/api/group-races/matches/{match_id}/open', path).then(unwrap)
        if (action === 'seed') await api.POST('/api/group-races/matches/{match_id}/seed', path).then(unwrap)
        if (action === 'announce') await api.POST('/api/group-races/matches/{match_id}/announce', path).then(unwrap)
        if (action === 'lock') await api.POST('/api/group-races/matches/{match_id}/lock', path).then(unwrap)
      } else {
        const path = { params: { path: { live_race_id: id } } }
        if (action === 'open') await api.POST('/api/group-races/live-races/{live_race_id}/open', path).then(unwrap)
        if (action === 'seed') await api.POST('/api/group-races/live-races/{live_race_id}/seed', path).then(unwrap)
        if (action === 'announce') await api.POST('/api/group-races/live-races/{live_race_id}/announce', path).then(unwrap)
        if (action === 'lock') await api.POST('/api/group-races/live-races/{live_race_id}/lock', path).then(unwrap)
      }
      setNote({ tone: 'info', text: done })
      race.reload()
      onChange()
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    } finally {
      setBusy(null)
    }
  }

  async function cancel(): Promise<boolean> {
    try {
      if (kind === 'match') {
        await api.DELETE('/api/group-races/matches/{match_id}', { params: { path: { match_id: id } } }).then(unwrap)
      } else {
        await api.DELETE('/api/group-races/live-races/{live_race_id}', { params: { path: { live_race_id: id } } }).then(unwrap)
      }
      onCancelled()
      return true
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
      return false
    }
  }

  const isOpen = r.state === 'open'
  const items: MenuItem[] = []
  if (r.state === 'scheduled') items.push({ label: 'Open the room now', icon: 'meeting_room', onSelect: () => void act('open', 'The room is open.') })
  if (isOpen && !r.seed_posted_at) items.push({ label: 'Post the seed now', icon: 'casino', onSelect: () => void act('seed', 'The seed is in the room.') })
  if (isOpen && r.channel_id) items.push({ label: 'Re-post announcement', icon: 'campaign', onSelect: () => void act('announce', 'Posted in Discord again.') })
  if (isOpen && !r.locked_at) items.push({ label: 'Lock the room now', icon: 'lock', onSelect: () => void act('lock', 'The room is locked.') })
  if (r.state !== 'finished') items.push({ label: 'Cancel race…', icon: 'event_busy', destructive: true, onSelect: () => setCancelling(true) })

  const actions = (
    <>
      {r.room_url && (
        <Button variant="outline" icon="open_in_new" asChild>
          <a href={r.room_url} target="_blank" rel="noopener noreferrer">
            Open in racetime
          </a>
        </Button>
      )}
      {items.length > 0 && <ActionsMenu items={items} />}
    </>
  )

  return (
    <Detail
      title={r.title}
      sub={
        <span className="gr-detail-sub">
          <StateLine race={r} zone={zone} />
          <span>
            {r.series} · {r.from_speedgaming ? 'from SpeedGaming' : 'added by hand'}
          </span>
        </span>
      }
      actions={actions}
    >
      {busy && <Spinner label="Working…" />}
      {note && <Banner tone={note.tone}>{note.text}</Banner>}
      <DetailSection title={r.scheduled_at ? dayHeading(r.scheduled_at, zone) : 'The race'}>
        <ol className="gr-steps">
          <Steps race={r} zone={zone} channel={channel} role={role} />
        </ol>
      </DetailSection>
      <Entrants race={r} />
      {!phone && r.channel_id && (
        <DetailSection title="Discord post">
          <DiscordPostPreview
            title={r.title}
            channel={channel}
            role={role}
            seedMinutes={r.scheduled_at && r.seed_at ? Math.round((Date.parse(r.scheduled_at) - Date.parse(r.seed_at)) / 60000) : 0}
            locks={r.lock_at_seed}
          />
        </DetailSection>
      )}
      <ConfirmDialog open={cancelling} onOpenChange={setCancelling} title="Cancel this race?" action="Cancel race" onConfirm={cancel}>
        {kind === 'match'
          ? 'The race comes off the schedule'
          : 'The live race is removed from the qualifier'}
        {r.room_url ? ' and its racetime room is marked cancelled here. Close the room on racetime.gg too.' : '.'}
      </ConfirmDialog>
    </Detail>
  )
}

function Step({ done, icon, title, sub, at }: { done: boolean; icon: IconName; title: ReactNode; sub?: ReactNode; at?: string | null }) {
  return (
    <li className={`gr-step${done ? ' gr-step--done' : ''}`}>
      <span className="gr-step__mark" aria-hidden="true">
        <Icon name={done ? 'check' : icon} size={18} />
      </span>
      <span className="gr-step__text">
        <span className="gr-step__title">
          {title}
          {done && <span className="visually-hidden"> (done)</span>}
        </span>
        {sub && <span className="gr-step__sub">{sub}</span>}
      </span>
      {at && <span className="gr-step__at">{at}</span>}
    </li>
  )
}

function Steps({ race: r, zone, channel, role }: { race: RaceDetail; zone: string; channel: string | null; role: string | null }) {
  const time = (iso?: string | null) => (iso ? formatTime(iso, zone) : null)
  return (
    <>
      <Step
        done={!!r.opened_at}
        icon="meeting_room"
        title={r.opened_at ? 'Room opened, open to all' : 'Room opens, open to all'}
        sub={r.room_url ? <a href={r.room_url} target="_blank" rel="noopener noreferrer">{r.room_url.replace(/^https?:\/\//, '')}</a> : undefined}
        at={time(r.opened_at ?? r.opens_at)}
      />
      {r.channel_id ? (
        <Step
          done={!!r.announced_at}
          icon="campaign"
          title={`${r.announced_at ? 'Announced' : 'Announces'} in #${channel}`}
          sub={role ? `Pings @${role}` : 'No role ping'}
          at={time(r.announced_at)}
        />
      ) : (
        <Step done={false} icon="campaign" title="Not posted in Discord" sub="Pick a channel in the series' racetime settings to post it." />
      )}
      <Step
        done={!!r.seed_posted_at}
        icon="casino"
        title={r.seed_posted_at ? 'Seed posted in the room' : 'Seed posts in the room'}
        sub={r.seed_note}
        at={time(r.seed_posted_at ?? r.seed_at)}
      />
      <Step
        done={!!r.locked_at}
        icon={r.lock_at_seed || r.locked_at ? 'lock' : 'lock_open'}
        title={r.locked_at ? 'Room locked' : r.lock_at_seed ? 'Room locks with the seed' : 'Room stays open'}
        sub={r.lock_at_seed || r.locked_at ? 'Nobody can join after that' : 'Locking is off for this series'}
        at={time(r.locked_at)}
      />
      <Step
        done={r.state === 'finished'}
        icon="flag"
        title={r.state === 'finished' ? 'Race finished' : 'Race starts'}
        sub={r.kind === 'match' ? 'Finish order is recorded on the race' : 'Runs are recorded on the qualifier'}
        at={time(r.scheduled_at)}
      />
    </>
  )
}

function finishTime(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

function Entrants({ race: r }: { race: RaceDetail }) {
  const unlinked = r.entrants.filter((e) => !e.linked).length
  const title = (
    <>
      Entrants · {r.entrants.length}
      {unlinked > 0 && <span className="gr-muted"> · {unlinked} haven't linked racetime here</span>}
    </>
  )
  let body
  if (r.entrants_problem) body = <Banner tone="warning">{r.entrants_problem}</Banner>
  else if (r.entrants.length === 0)
    body = <p className="gr-muted">{r.state === 'scheduled' ? "The room isn't open yet." : r.state === 'finished' ? 'Nobody finished.' : "Nobody's in the room yet."}</p>
  else
    body = (
      <ul className="gr-entrants">
        {r.entrants.map((e, i) => (
          <li key={`${e.name}-${i}`} className="gr-entrant">
            {e.place != null && <span className="gr-entrant__place">{e.place}.</span>}
            <Icon name={e.linked ? 'person' : 'link_off'} size={18} />
            <span className="gr-entrant__name">{e.name}</span>
            {!e.linked && <Chip tone="pending">Not linked</Chip>}
            {e.finish_seconds != null && <span className="gr-entrant__time">{finishTime(e.finish_seconds)}</span>}
          </li>
        ))}
      </ul>
    )
  return <DetailSection title={title}>{body}</DetailSection>
}

function NewRace({ zone, onClose, onAdded }: { zone: string | null; onClose: () => void; onAdded: (race: RaceDetail) => void }) {
  const series = useAdminQuery('group-races/series', () => api.GET('/api/group-races/series').then(unwrap))
  const [pick, setPick] = useState('')
  const [permalink, setPermalink] = useState('')
  const [title, setTitle] = useState('')
  const [start, setStart] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const list = series.data ?? []
  const chosen = list.find((s) => `${s.kind}:${s.id}` === pick)

  async function add() {
    if (!chosen) return setError('Pick a series for the race.')
    if (!start) return setError('Pick a start time.')
    setBusy(true)
    setError(null)
    try {
      const body =
        chosen.kind === 'tournament'
          ? { title: title.trim(), scheduled_at: start, tournament_id: chosen.id }
          : { title: title.trim(), scheduled_at: start, pool_id: chosen.id, permalink_id: permalink ? Number(permalink) : null }
      onAdded(await api.POST('/api/group-races', { body }).then(unwrap))
      onClose()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  const tournaments = list.filter((s) => s.kind === 'tournament')
  const pools = list.filter((s) => s.kind === 'pool')
  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="New group race"
      description="The bot opens the room, posts it and posts the seed on the series' schedule."
      primaryLabel="Add race"
      onPrimary={() => void add()}
      busy={busy}
      busyLabel="Adding…"
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        {series.error && <Banner tone="error">{series.error.message}</Banner>}
        {series.data && list.length === 0 && (
          <Banner tone="info">No series takes group races yet. Turn on Group race on a tournament's Racetime tab first.</Banner>
        )}
        <label className="field">
          <span className="field__label">Series</span>
          <select
            className="input"
            value={pick}
            onChange={(e) => {
              setPick(e.target.value)
              setPermalink('')
            }}
          >
            <option value="">Pick one</option>
            {tournaments.length > 0 && (
              <optgroup label="Tournaments">
                {tournaments.map((s) => (
                  <option key={`t${s.id}`} value={`${s.kind}:${s.id}`}>
                    {s.name}
                  </option>
                ))}
              </optgroup>
            )}
            {pools.length > 0 && (
              <optgroup label="Qualifier pools">
                {pools.map((s) => (
                  <option key={`p${s.id}`} value={`${s.kind}:${s.id}`}>
                    {s.name}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </label>
        {chosen?.kind === 'pool' && (
          <label className="field">
            <span className="field__label">Permalink</span>
            <select className="input" value={permalink} onChange={(e) => setPermalink(e.target.value)}>
              <option value="">Pick it later</option>
              {(chosen.permalinks ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <span className="field__hint">The seed everyone races. Only the pool's live race permalinks are listed.</span>
          </label>
        )}
        <label className="field">
          <span className="field__label">Title</span>
          <input className="input" value={title} placeholder="e.g. ALTTPR Daily — Open" onChange={(e) => setTitle(e.target.value)} />
          <span className="field__hint">Shown in Discord and on racetime. "Co-op" in the title makes it a team race.</span>
        </label>
        <InstantField label="Starts" value={start} zone={zone} onChange={setStart} />
      </div>
    </FormDialog>
  )
}
