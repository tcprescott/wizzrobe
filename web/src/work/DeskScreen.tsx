import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, unwrap } from '../api/client'
import {
  ActionsMenu,
  Banner,
  Button,
  Chip,
  Dialog,
  EmptyState,
  FormDialog,
  Icon,
  IconButton,
  PageHeader,
  Panel,
  PanelRow,
  RowMenu,
  Spinner,
  type MenuItem,
} from '../components'
import { useCommunity } from '../community/CommunityProvider'
import { effectiveZone, formatTime } from '../lib/time'
import { done, message, useEventQuery } from '../me/data'
import { usePolling, useWorkQuery, type Desk, type DeskRow, type Member } from './data'
import { canScanBadges, ScanBadgeDialog } from './ScanBadge'

const FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'not_yet', label: 'Not yet' },
  { value: 'checked_in', label: 'Checked in' },
  { value: 'comped', label: 'Comped' },
  { value: 'walk_up', label: 'Walk-ups' },
] as const
type Filter = (typeof FILTERS)[number]['value']

const LANYARD: Record<string, string> = { staff: 'Staff', volunteer: 'Volunteer', vip: 'VIP', base: 'Base', day_pass: 'Day Pass' }
const DM_TEXT: Record<string, string> = {
  pending: 'Sending the check-in DM',
  sent: 'Check-in DM reaches them',
  blocked: "Can't DM them",
  opted_out: 'DMs off in Wizzrobe',
  failed: 'Check-in DM not tested (Discord down)',
}

function isCheckedIn(row: DeskRow): boolean {
  return row.checked_in_at != null
}
function isWithdrawn(row: DeskRow): boolean {
  return row.withdrawn_at != null && !isCheckedIn(row)
}
function inFilter(row: DeskRow, filter: Filter): boolean {
  if (filter === 'all') return true
  if (isWithdrawn(row)) return false
  if (filter === 'checked_in') return isCheckedIn(row)
  if (filter === 'not_yet') return !isCheckedIn(row)
  if (filter === 'comped') return (row.comp_reasons ?? []).length > 0
  return row.source === 'walk_up'
}
function matches(row: DeskRow, needle: string): boolean {
  if (!needle) return true
  const hay = [row.display_name, row.user_name, row.username, ...(row.badge_codes ?? [])].filter(Boolean).join(' ').toLowerCase()
  return needle
    .toLowerCase()
    .split(/\s+/)
    .every((part) => hay.includes(part))
}
/** An unlinked badge buyer: checking them in asks for their account first. */
function asksForAccount(row: DeskRow): boolean {
  return row.user_id == null && row.source === 'matcherino' && row.link_method !== 'manual'
}

/**
 * The check-in desk: one event's roster with the search as the main control.
 * Opens straight on the only open event, else lists the open ones.
 */
export function DeskScreen() {
  const { eventId } = useParams()
  const { base } = useCommunity()
  const query = useEventQuery()
  const events = useWorkQuery(eventId ? null : 'desk-events', () => api.GET('/api/check-in/events', { params: { query } }).then(unwrap))

  if (eventId) return <DeskForEvent eventId={Number(eventId)} />
  if (events.error && !events.data) {
    return (
      <>
        <PageHeader title="Check-in desk" />
        <Banner tone="error" action={<Button variant="outline" onClick={events.reload}>Try again</Button>}>
          {events.error.message}
        </Banner>
      </>
    )
  }
  if (!events.data) return <Spinner label="Loading check-in events" />
  const open = events.data.filter((e) => e.status === 'open')
  const only = open[0]
  if (open.length === 1 && only) return <DeskForEvent eventId={only.id} />
  const others = events.data.filter((e) => e.status !== 'open')
  return (
    <>
      <PageHeader title="Check-in desk" lede={open.length ? 'Pick the event you’re checking people in for.' : undefined} />
      {open.length === 0 && others.length === 0 ? (
        <EmptyState icon="how_to_reg" title="No event is open for check-in right now">
          Staff open one from Admin, under Check-in roster.
        </EmptyState>
      ) : (
        <Panel>
          {[...open, ...others].map((e) => (
            <PanelRow key={e.id}>
              <Link className="list-link" to={`${base}/work/desk/${e.id}`}>
                <Icon name="how_to_reg" />
                <span>
                  {e.name}
                  <span className="list-link__sub">{e.status === 'open' ? 'Open' : e.status === 'draft' ? 'Draft, staff only' : 'Closed'}</span>
                </span>
                <Icon name="chevron_right" />
              </Link>
            </PanelRow>
          ))}
        </Panel>
      )}
    </>
  )
}

interface Toast {
  text: string
  sub?: string
  undo?: () => void
}

function DeskForEvent({ eventId }: { eventId: number }) {
  const query = useEventQuery()
  const desk = useWorkQuery(`desk/${eventId}`, () =>
    api.GET('/api/check-in/events/{event_id}/desk', { params: { path: { event_id: eventId }, query } }).then(unwrap),
  )
  usePolling(desk.reload)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [toast, setToast] = useState<Toast | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [linking, setLinking] = useState<{ row: DeskRow; thenCheckIn: boolean } | null>(null)
  const [confirm, setConfirm] = useState<{ title: string; body: string; label: string; run: () => Promise<void>; destructive?: boolean } | null>(null)
  const [walkUp, setWalkUp] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [scanning, setScanning] = useState(false)
  const searchRef = useRef<HTMLInputElement>(null)
  const toastTimer = useRef<number | undefined>(undefined)

  useEffect(() => () => window.clearTimeout(toastTimer.current), [])

  const data = desk.data
  const zone = effectiveZone(data?.zone)
  const rows = useMemo(() => {
    const list = (data?.rows ?? []).filter((r) => inFilter(r, filter) && matches(r, search.trim()))
    return list.sort((a, b) => a.display_name.localeCompare(b.display_name))
  }, [data, filter, search])

  if (desk.error && !data) {
    return (
      <>
        <PageHeader title="Check-in desk" />
        <Banner tone="error" action={<Button variant="outline" onClick={desk.reload}>Try again</Button>}>
          {desk.error.message}
        </Banner>
      </>
    )
  }
  if (!data) return <Spinner label="Loading the roster" />

  const present = data.rows.filter((r) => !isWithdrawn(r))
  const checkedIn = present.filter(isCheckedIn).length
  const pct = present.length ? Math.round((checkedIn / present.length) * 100) : 0
  const canSync = data.event.matcherino_venue_id != null

  function showToast(next: Toast) {
    window.clearTimeout(toastTimer.current)
    setToast(next)
    toastTimer.current = window.setTimeout(() => setToast(null), 8000)
  }

  async function act(run: () => Promise<unknown>, ok?: string) {
    setError(null)
    try {
      await run()
      if (ok) showToast({ text: ok })
    } catch (err) {
      setError(message(err))
    } finally {
      desk.reload()
    }
  }

  async function checkIn(row: DeskRow, despiteWithdrawn = false) {
    setError(null)
    try {
      const result = await api
        .POST('/api/check-in/entrants/{entrant_id}/check-in', {
          params: { path: { entrant_id: row.id }, query },
          body: { despite_withdrawn: despiteWithdrawn },
        })
        .then(unwrap)
      const after = result.entrant
      if (result.already) {
        const at = after.checked_in_at ? formatTime(after.checked_in_at, zone) : ''
        showToast({ text: `${after.display_name} was already checked in by ${after.checked_in_by_name ?? 'someone'}${at ? ` at ${at}` : ''}.` })
      } else {
        const lanyard = after.lanyard ? LANYARD[after.lanyard] ?? after.lanyard : ''
        const extra = (after.badges ?? []).slice(1).map((b) => `${b} badge`).join(', ')
        showToast({
          text: `Checked in ${after.display_name}`,
          sub: lanyard ? `Hand over: ${lanyard} lanyard${extra ? `, plus ${extra}` : ''}` : undefined,
          undo: () =>
            void act(
              () => api.DELETE('/api/check-in/entrants/{entrant_id}/check-in', { params: { path: { entrant_id: after.id }, query } }).then(unwrap),
              `Undid check-in for ${after.display_name}.`,
            ),
        })
      }
      setSearch('')
      searchRef.current?.focus()
    } catch (err) {
      setError(message(err))
    } finally {
      desk.reload()
    }
  }

  function onCheckIn(row: DeskRow) {
    if (asksForAccount(row)) setLinking({ row, thenCheckIn: true })
    else void checkIn(row)
  }

  function menuFor(row: DeskRow): MenuItem[] {
    const items: MenuItem[] = []
    const name = row.display_name
    const ownAccount = row.source === 'comp' || row.source === 'wizzrobe'
    if (row.user_id == null) {
      if (!ownAccount) items.push({ label: 'Link account…', icon: 'link', onSelect: () => setLinking({ row, thenCheckIn: false }) })
    } else if (!ownAccount) {
      items.push({
        label: 'Unlink account',
        icon: 'link_off',
        onSelect: () =>
          void act(
            () => api.DELETE('/api/check-in/entrants/{entrant_id}/user', { params: { path: { entrant_id: row.id }, query } }).then(unwrap),
            `Unlinked ${name}.`,
          ),
      })
    }
    if (isCheckedIn(row) && row.has_discord) {
      items.push({
        label: 'Send the check-in DM again',
        icon: 'send',
        onSelect: () =>
          void act(
            () => api.POST('/api/check-in/entrants/{entrant_id}/test-dm', { params: { path: { entrant_id: row.id }, query } }).then(unwrap),
            `Sending ${name} a test DM.`,
          ),
      })
    }
    if (isCheckedIn(row)) {
      items.push({
        label: 'Undo check-in',
        icon: 'undo',
        onSelect: () =>
          void act(
            () => api.DELETE('/api/check-in/entrants/{entrant_id}/check-in', { params: { path: { entrant_id: row.id }, query } }).then(unwrap),
            `Undid check-in for ${name}.`,
          ),
      })
    }
    if (data?.can_manage && row.source === 'walk_up') {
      items.push({
        label: 'Remove walk-up…',
        icon: 'delete',
        destructive: true,
        onSelect: () =>
          setConfirm({
            title: `Remove ${name}?`,
            body: 'Their check-in goes with them.',
            label: 'Remove',
            destructive: true,
            run: () =>
              act(
                () => api.DELETE('/api/check-in/entrants/{entrant_id}', { params: { path: { entrant_id: row.id }, query } }).then(done),
                `Removed ${name}.`,
              ),
          }),
      })
    }
    return items
  }

  async function sync() {
    setSyncing(true)
    await act(async () => {
      const r = await api.POST('/api/check-in/events/{event_id}/sync', { params: { path: { event_id: eventId }, query } }).then(unwrap)
      showToast({ text: `Synced: ${r.added} new, ${r.withdrawn} withdrawn.` })
    })
    setSyncing(false)
  }

  const actions: MenuItem[] = []
  if (canSync) actions.push({ label: syncing ? 'Syncing…' : 'Sync badges and comps now', icon: 'sync', disabled: syncing, onSelect: () => void sync() })

  return (
    <>
      <PageHeader
        title={data.event.name}
        lede={data.event.status === 'closed' ? 'This event is closed. You can still record late check-ins.' : 'Check-in desk'}
        actions={
          <>
            {data.can_manage && (
              <Button variant="outline" icon="person_add" onClick={() => setWalkUp(true)}>
                Walk-up
              </Button>
            )}
            {actions.length > 0 && <ActionsMenu items={actions} />}
          </>
        }
      />
      <div className="work-toolbar">
        <label className="work-search">
          <Icon name="search" />
          <span className="visually-hidden">Search the roster</span>
          <input
            ref={searchRef}
            type="search"
            value={search}
            placeholder="Name or badge code"
            autoComplete="off"
            onChange={(e) => setSearch(e.target.value)}
          />
          {canScanBadges() && <IconButton icon="qr_code_scanner" label="Scan a badge QR" onClick={() => setScanning(true)} />}
        </label>
      </div>
      <ScanBadgeDialog open={scanning} onOpenChange={setScanning} onCode={setSearch} />
      <div className="filter-chips" role="radiogroup" aria-label="Show">
        {FILTERS.map((f) => (
          <span key={f.value}>
            <input type="radio" id={`desk-${f.value}`} name="desk-filter" checked={filter === f.value} onChange={() => setFilter(f.value)} />
            <label htmlFor={`desk-${f.value}`}>{f.label}</label>
          </span>
        ))}
      </div>
      <div className="work-progress">
        <span className="work-progress__bar" role="progressbar" aria-label="Checked in" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
          <i style={{ width: `${pct}%` }} />
        </span>
        <span>
          <b>{checkedIn}</b> of {present.length} in
          {data.event.last_synced_at ? ` · synced ${formatTime(data.event.last_synced_at, zone)}` : ''}
        </span>
      </div>
      {data.event.last_sync_error && <Banner tone="warning">The last sync failed: {data.event.last_sync_error}</Banner>}
      {error && <Banner tone="error">{error}</Banner>}
      <Panel>
        {rows.length === 0 ? (
          <PanelRow>
            <EmptyState icon="search" title={search ? 'Nobody matches that search' : 'Nobody here yet'} />
          </PanelRow>
        ) : (
          rows.map((row) => (
            <PanelRow key={row.id}>
              <DeskRowView row={row} zone={zone} menu={menuFor(row)} onCheckIn={() => onCheckIn(row)} canManage={data.can_manage} onCheckInAnyway={() =>
                setConfirm({
                  title: `Check in ${row.display_name} anyway?`,
                  body: `${row.display_name} doesn't hold a badge any more.`,
                  label: 'Check in anyway',
                  run: () => checkIn(row, true),
                })
              } />
            </PanelRow>
          ))
        )}
      </Panel>
      {toast && (
        <div className="work-toast" role="status">
          <span>
            {toast.text}
            {toast.sub && (
              <>
                <br />
                <b>{toast.sub}</b>
              </>
            )}
          </span>
          {toast.undo && (
            <button
              type="button"
              onClick={() => {
                toast.undo?.()
                setToast(null)
              }}
            >
              Undo
            </button>
          )}
        </div>
      )}
      {linking && (
        <LinkDialog
          desk={data}
          row={linking.row}
          thenCheckIn={linking.thenCheckIn}
          onClose={() => setLinking(null)}
          onLinked={async (member) => {
            setLinking(null)
            if (member) showToast({ text: `Linked to ${member.name}.` })
            if (linking.thenCheckIn) await checkIn(linking.row)
            else desk.reload()
          }}
        />
      )}
      {walkUp && <WalkUpDialog desk={data} onClose={() => setWalkUp(false)} onAdded={(text) => { setWalkUp(false); showToast({ text }); desk.reload() }} />}
      {confirm && (
        <FormDialog
          open
          onOpenChange={(o) => !o && setConfirm(null)}
          size="sm"
          title={confirm.title}
          description={confirm.body}
          primaryLabel={confirm.label}
          destructive={confirm.destructive}
          onPrimary={() => {
            const run = confirm.run
            setConfirm(null)
            void run()
          }}
        >
          {null}
        </FormDialog>
      )}
    </>
  )
}

function DeskRowView({
  row,
  zone,
  menu,
  onCheckIn,
  canManage,
  onCheckInAnyway,
}: {
  row: DeskRow
  zone: string
  menu: MenuItem[]
  onCheckIn: () => void
  canManage: boolean
  onCheckInAnyway: () => void
}) {
  const linkedAs = row.user_name && row.user_name !== row.display_name ? `Account: ${row.user_name}` : undefined
  const sub = row.user_id == null ? (row.suggestions ? `Not linked · ${row.suggestions} suggestion${row.suggestions === 1 ? '' : 's'}` : 'Not linked') : linkedAs
  const dm = row.dm_status ? DM_TEXT[row.dm_status] : undefined
  return (
    <div className="desk-row">
      <span className="desk-row__disc" aria-hidden="true">
        {(row.display_name[0] ?? '?').toUpperCase()}
      </span>
      <span>
        <span className="desk-row__name">{row.display_name}</span>
        {(sub || (row.comp_reasons ?? []).length > 0) && (
          <>
            <br />
            <span className={`desk-row__sub${row.user_id == null && row.suggestions ? ' desk-row__hint' : ''}`}>
              {[sub, (row.comp_reasons ?? []).length > 0 ? 'Comped' : undefined].filter(Boolean).join(' · ')}
            </span>
          </>
        )}
      </span>
      <span className="desk-row__lanyard">{row.lanyard && <span className={`lanyard lanyard--${row.lanyard}`}>{LANYARD[row.lanyard] ?? row.lanyard}</span>}</span>
      <span className="desk-row__account">{row.username ?? ''}</span>
      <span className="desk-row__acts">
        {isCheckedIn(row) ? (
          <Chip tone="ok">
            Checked in {row.checked_in_at ? formatTime(row.checked_in_at, zone) : ''}
            {dm && dm !== DM_TEXT.sent ? ` · ${dm}` : ''}
          </Chip>
        ) : isWithdrawn(row) ? (
          canManage ? (
            <Button variant="outline" onClick={onCheckInAnyway}>
              Check in anyway
            </Button>
          ) : (
            <Chip tone="pending">{row.source === 'comp' ? 'No longer comped' : 'No badge'}</Chip>
          )
        ) : (
          <Button variant="primary" icon="how_to_reg" onClick={onCheckIn} aria-label={`Check in ${row.display_name}`}>
            Check in
          </Button>
        )}
        {menu.length > 0 && <RowMenu label={`More for ${row.display_name}`} items={menu} />}
      </span>
    </div>
  )
}

function MemberPicker({ results, onPick, empty }: { results: Member[] | null; onPick: (m: Member) => void; empty: string }) {
  if (results === null) return null
  if (results.length === 0) return <p className="desk-row__sub">{empty}</p>
  return (
    <ul className="member-results">
      {results.map((m) => (
        <li key={m.id}>
          <Button variant="ghost" icon="person" onClick={() => onPick(m)}>
            {m.name}
            {m.username && m.username !== m.name ? ` (${m.username})` : ''}
          </Button>
        </li>
      ))}
    </ul>
  )
}

function useMemberSearch(eventId: number, forLink: boolean) {
  const query = useEventQuery()
  const [q, setQ] = useState('')
  const [results, setResults] = useState<Member[] | null>(null)
  useEffect(() => {
    const term = q.trim()
    if (term.length < 2) {
      setResults(null)
      return
    }
    let live = true
    const id = window.setTimeout(() => {
      api
        .GET('/api/check-in/events/{event_id}/members', { params: { path: { event_id: eventId }, query: { ...query, q: term, for_link: forLink } } })
        .then(unwrap)
        .then((r) => live && setResults(r))
        .catch(() => live && setResults([]))
    }, 300)
    return () => {
      live = false
      window.clearTimeout(id)
    }
  }, [q, eventId, forLink, query])
  return { q, setQ, results }
}

function LinkDialog({
  desk,
  row,
  thenCheckIn,
  onClose,
  onLinked,
}: {
  desk: Desk
  row: DeskRow
  thenCheckIn: boolean
  onClose: () => void
  onLinked: (member: Member | null) => Promise<void>
}) {
  const query = useEventQuery()
  const [suggested, setSuggested] = useState<Member[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const search = useMemberSearch(desk.event.id, true)

  useEffect(() => {
    api
      .GET('/api/check-in/entrants/{entrant_id}/suggestions', { params: { path: { entrant_id: row.id }, query } })
      .then(unwrap)
      .then(setSuggested)
      .catch(() => setSuggested([]))
  }, [row.id, query])

  async function pick(member: Member) {
    setError(null)
    try {
      await api.PUT('/api/check-in/entrants/{entrant_id}/user', { params: { path: { entrant_id: row.id }, query }, body: { user_id: member.id } }).then(unwrap)
      await onLinked(member)
    } catch (err) {
      setError(message(err))
    }
  }

  const body = (
    <div className="work-form">
      {thenCheckIn && (
        <p>
          Their badge isn't linked to a Wizzrobe account yet. Ask for their Discord or Twitch name and pick their account, or skip if they don't
          have one.
        </p>
      )}
      {row.signed_in_with && <p className="desk-row__sub">Signed in to Matcherino with {row.signed_in_with}.</p>}
      <h3 className="work-subhead">Suggestions</h3>
      {suggested === null ? <Spinner label="Looking for matches" /> : <MemberPicker results={suggested} onPick={(m) => void pick(m)} empty="No one in this community looks like a match." />}
      <label className="field">
        <span className="field__label">Search members</span>
        <input className="input" type="search" value={search.q} placeholder="Name, username or Twitch" onChange={(e) => search.setQ(e.target.value)} />
      </label>
      <MemberPicker results={search.results} onPick={(m) => void pick(m)} empty="No members match." />
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </div>
  )

  if (thenCheckIn) {
    return (
      <FormDialog open onOpenChange={(o) => !o && onClose()} title={`Check in ${row.display_name}`} primaryLabel="Skip, just check in" onPrimary={() => void onLinked(null)}>
        {body}
      </FormDialog>
    )
  }
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={`Link ${row.display_name}`}>
      {body}
    </Dialog>
  )
}

function WalkUpDialog({ desk, onClose, onAdded }: { desk: Desk; onClose: () => void; onAdded: (text: string) => void }) {
  const query = useEventQuery()
  const [mode, setMode] = useState<'member' | 'name'>('member')
  const [picked, setPicked] = useState<Member | null>(null)
  const [name, setName] = useState('')
  const [checkNow, setCheckNow] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const search = useMemberSearch(desk.event.id, false)

  async function submit() {
    if (mode === 'member' && !picked) {
      setError('Pick a member first.')
      return
    }
    if (mode === 'name' && !name.trim()) {
      setError('Type their name first.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const body = mode === 'member' ? { user_id: picked?.id, check_in: checkNow } : { name: name.trim(), check_in: checkNow }
      const added = await api.POST('/api/check-in/events/{event_id}/walk-ups', { params: { path: { event_id: desk.event.id }, query }, body }).then(unwrap)
      onAdded(`${added.display_name} added${checkNow ? ' and checked in' : ''}.`)
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <FormDialog open onOpenChange={(o) => !o && onClose()} size="sm" title="Add walk-up" primaryLabel="Add" busy={busy} busyLabel="Adding…" onPrimary={() => void submit()}>
      <div className="work-form">
        <div className="segmented" role="radiogroup" aria-label="Who">
          {(
            [
              ['member', 'A member'],
              ['name', 'Just a name'],
            ] as const
          ).map(([value, label]) => (
            <span key={value}>
              <input type="radio" id={`wu-${value}`} name="wu-mode" checked={mode === value} onChange={() => setMode(value)} />
              <label htmlFor={`wu-${value}`}>{label}</label>
            </span>
          ))}
        </div>
        {mode === 'member' ? (
          <>
            <label className="field">
              <span className="field__label">Search members</span>
              <input className="input" type="search" value={search.q} onChange={(e) => search.setQ(e.target.value)} />
            </label>
            <p className="desk-row__sub" role="status">
              {picked ? `Picked: ${picked.name}` : 'No member picked yet.'}
            </p>
            <MemberPicker results={search.results} onPick={setPicked} empty="No members match." />
          </>
        ) : (
          <label className="field">
            <span className="field__label">Their name</span>
            <input className="input" value={name} maxLength={255} onChange={(e) => setName(e.target.value)} />
          </label>
        )}
        <label className="work-check">
          <input type="checkbox" checked={checkNow} onChange={(e) => setCheckNow(e.target.checked)} />
          Check them in now
        </label>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </FormDialog>
  )
}
