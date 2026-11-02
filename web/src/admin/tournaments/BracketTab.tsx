import { useState } from 'react'
import { api, unwrap } from '../../api/client'
import { Banner, Button, Chip, EmptyState, FormDialog, Panel, PanelRow, RowMenu, Spinner, type MenuItem } from '../../components'
import { effectiveZone, formatDayTime } from '../../lib/time'
import { done, message } from '../../me/data'
import { useAdminQuery, type Board, type BoardRow, type Entrant, type Person } from './data'

type Note = { tone: 'info' | 'error'; text: string } | null

/** The bracket site this tournament follows: link it, sync it, step its stages, match its entrants. */
export function BracketTab({ row, board, onChange }: { row: BoardRow; board: Board; onChange: () => void }) {
  const [note, setNote] = useState<Note>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [unlinking, setUnlinking] = useState(false)
  const [stepping, setStepping] = useState(false)
  const b = row.bracket
  const zone = effectiveZone(board.zone)

  async function run(key: string, call: () => Promise<string>) {
    setBusy(key)
    setNote(null)
    try {
      setNote({ tone: 'info', text: await call() })
      onChange()
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    } finally {
      setBusy(null)
    }
  }
  const sync = () =>
    run('sync', async () => {
      const r = await api
        .POST('/api/admin/tournaments/{tournament_id}/bracket/sync', { params: { path: { tournament_id: row.id } } })
        .then(unwrap)
      return `Synced from ${r.source_label}: ${r.summary}.`
    })

  if (!b) {
    return (
      <>
        {note && <Banner tone={note.tone}>{note.text}</Banner>}
        {board.linkable_sites.length > 0 && row.can_link ? (
          <LinkBracket row={row} board={board} onLinked={(text) => { setNote({ tone: 'info', text }); onChange() }} />
        ) : (
          <EmptyState icon="account_tree" title="No bracket site linked">
            {row.can_link
              ? 'Turn on Challonge or start.gg for this community to link a bracket.'
              : 'A staff member can link this tournament to a Challonge or start.gg bracket.'}
          </EmptyState>
        )}
      </>
    )
  }

  const menu: MenuItem[] = []
  if (b.url) menu.push({ label: `Open on ${b.label}`, icon: 'open_in_new', onSelect: () => window.open(b.url!, '_blank', 'noopener') })
  if (row.can_link) menu.push({ label: `Unlink ${b.label} bracket…`, icon: 'link_off', destructive: true, onSelect: () => setUnlinking(true) })

  return (
    <>
      {note && (
        <Banner tone={note.tone} action={<Button variant="ghost" onClick={() => setNote(null)}>Dismiss</Button>}>
          {note.text}
        </Banner>
      )}
      <Panel
        title={`${b.label} bracket`}
        actions={menu.length > 0 ? <RowMenu label={`More for the ${b.label} bracket`} items={menu} /> : undefined}
      >
        <PanelRow>
          <dl className="t-kv">
            {b.stage && (
              <>
                <dt>Stage</dt>
                <dd>{b.stage}</dd>
              </>
            )}
            <dt>Last sync</dt>
            <dd>{b.synced_at ? formatDayTime(b.synced_at, zone) : 'Never'}</dd>
            <dt>Unmatched entrants</dt>
            <dd>{b.unmatched ? <Chip tone="pending">{b.unmatched}</Chip> : 'None'}</dd>
          </dl>
        </PanelRow>
        <PanelRow>
          <div className="t-actions">
            <Button icon="sync" onClick={() => void sync()} busy={busy === 'sync'} busyLabel="Syncing…">
              Sync now
            </Button>
            {b.stage_action && (
              <Button variant="outline" icon="skip_next" onClick={() => setStepping(true)}>
                {b.stage_action.label}…
              </Button>
            )}
          </div>
        </PanelRow>
      </Panel>
      {b.site === 'startgg' && <StartggPanel row={row} onChange={onChange} />}
      <EntrantsPanel row={row} onChange={onChange} />
      {unlinking && (
        <FormDialog
          open
          onOpenChange={(o) => !o && setUnlinking(false)}
          title="Unlink bracket?"
          primaryLabel="Unlink"
          destructive
          busy={busy === 'unlink'}
          busyLabel="Unlinking…"
          onPrimary={() =>
            void run('unlink', async () => {
              done(await api.DELETE('/api/admin/tournaments/{tournament_id}/bracket', { params: { path: { tournament_id: row.id } } }))
              setUnlinking(false)
              return `${row.name} unlinked from ${b.label}.`
            })
          }
        >
          <p style={{ margin: 0 }}>
            {b.site === 'challonge'
              ? `Its mirrored entrants and bracket matches are removed here. The bracket on Challonge is untouched, and linking it again re-imports it. Scheduled matches stay as they are.`
              : `Its pools and bracket are removed here and players can no longer book from it. The event on start.gg is untouched, and scheduled matches stay as they are.`}
          </p>
        </FormDialog>
      )}
      {stepping && b.stage_action && (
        <FormDialog
          open
          onOpenChange={(o) => !o && setStepping(false)}
          title={b.stage_action.label}
          primaryLabel={b.stage_action.label}
          busy={busy === 'stage'}
          busyLabel="Updating Challonge…"
          onPrimary={() =>
            void run('stage', async () => {
              await api
                .POST('/api/admin/tournaments/{tournament_id}/bracket/stage', {
                  params: { path: { tournament_id: row.id } },
                  body: { action: b.stage_action!.key },
                })
                .then(unwrap)
              setStepping(false)
              return `${row.name}: ${b.stage_action!.label.toLowerCase()} done.`
            })
          }
        >
          <p style={{ margin: 0 }}>{b.stage_action.confirm}</p>
        </FormDialog>
      )}
    </>
  )
}

function LinkBracket({ row, board, onLinked }: { row: BoardRow; board: Board; onLinked: (text: string) => void }) {
  const [site, setSite] = useState(board.linkable_sites[0]?.key ?? 'challonge')
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const label = board.linkable_sites.find((s) => s.key === site)?.label ?? site
  async function link() {
    setBusy(true)
    setError(null)
    try {
      await api
        .PUT('/api/admin/tournaments/{tournament_id}/bracket', {
          params: { path: { tournament_id: row.id } },
          body: { site: site as 'challonge' | 'startgg', bracket: value.trim() },
        })
        .then(unwrap)
      onLinked(`${row.name} linked to ${label}.`)
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Panel title="Link a bracket">
      <PanelRow>
        <form
          className="t-form"
          onSubmit={(e) => {
            e.preventDefault()
            void link()
          }}
        >
          {error && <Banner tone="error">{error}</Banner>}
          <p className="t-row__sub" style={{ margin: 0 }}>
            Wizzrobe mirrors the bracket's entrants and matchups so players can book their matches here.
          </p>
          {board.linkable_sites.length > 1 && (
            <label className="field">
              <span className="field__label">Site</span>
              <select className="input" value={site} onChange={(e) => setSite(e.target.value)}>
                {board.linkable_sites.map((s) => (
                  <option key={s.key} value={s.key}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="field">
            <span className="field__label">{label} link or id</span>
            <input
              className="input"
              value={value}
              placeholder={site === 'startgg' ? 'https://start.gg/tournament/…/event/…' : 'https://challonge.com/…'}
              onChange={(e) => setValue(e.target.value)}
            />
          </label>
          <div>
            <Button type="submit" icon="add_link" disabled={!value.trim()} busy={busy} busyLabel="Linking…">
              Link bracket
            </Button>
          </div>
        </form>
      </PanelRow>
    </Panel>
  )
}

function StartggPanel({ row, onChange }: { row: BoardRow; onChange: () => void }) {
  const settings = useAdminQuery(`tournaments/${row.id}/startgg`, () =>
    api.GET('/api/admin/tournaments/{tournament_id}/bracket/startgg', { params: { path: { tournament_id: row.id } } }).then(unwrap),
  )
  const [error, setError] = useState<string | null>(null)
  async function patch(body: { bookable_phase_id?: string | null; report_results?: boolean }) {
    setError(null)
    try {
      await api
        .PATCH('/api/admin/tournaments/{tournament_id}/bracket/startgg', { params: { path: { tournament_id: row.id } }, body })
        .then(unwrap)
      settings.reload()
      onChange()
    } catch (err) {
      setError(message(err))
    }
  }
  const s = settings.data
  return (
    <Panel title="start.gg settings">
      <PanelRow>
        {!s ? (
          <Spinner label="Loading start.gg settings" />
        ) : (
          <div className="t-form">
            {error && <Banner tone="error">{error}</Banner>}
            <label className="field">
              <span className="field__label">Players book from</span>
              <select className="input" value={s.bookable_phase_id ?? ''} onChange={(e) => void patch({ bookable_phase_id: e.target.value || null })}>
                <option value="">Every phase</option>
                {s.phases.map((p) => (
                  <option key={p.phase_id} value={p.phase_id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="t-check">
              <input type="checkbox" checked={s.report_results} onChange={(e) => void patch({ report_results: e.target.checked })} />
              Write confirmed results back to start.gg
            </label>
          </div>
        )}
      </PanelRow>
    </Panel>
  )
}

const STATUS: Record<Entrant['status'], { label: string; tone: 'ok' | 'info' | 'pending' }> = {
  linked: { label: 'Linked account', tone: 'ok' },
  manual: { label: 'Matched by hand', tone: 'info' },
  suggested: { label: 'Suggested', tone: 'pending' },
  unmatched: { label: 'Unmatched', tone: 'pending' },
}

function EntrantsPanel({ row, onChange }: { row: BoardRow; onChange: () => void }) {
  const entrants = useAdminQuery(`tournaments/${row.id}/entrants`, () =>
    api.GET('/api/admin/tournaments/{tournament_id}/bracket/entrants', { params: { path: { tournament_id: row.id } } }).then(unwrap),
  )
  const [matching, setMatching] = useState<Entrant | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [onlyOpen, setOnlyOpen] = useState(true)
  const refresh = () => {
    entrants.reload()
    onChange()
  }
  async function act(call: () => Promise<{ error?: unknown; response: Response }>) {
    setError(null)
    try {
      done(await call())
      refresh()
    } catch (err) {
      setError(message(err))
    }
  }
  if (entrants.error && !entrants.data) {
    return (
      <Banner tone="error" action={<Button variant="outline" onClick={entrants.reload}>Try again</Button>}>
        {entrants.error.message}
      </Banner>
    )
  }
  const data = entrants.data
  const path = (entrant_id: number) => ({ params: { path: { tournament_id: row.id, entrant_id } } })
  const open = data?.entrants.filter((e) => e.status === 'unmatched' || e.status === 'suggested') ?? []
  const suggested = open.filter((e) => e.status === 'suggested').length
  const shown = onlyOpen ? open : (data?.entrants ?? [])

  return (
    <Panel
      title="Entrants"
      actions={
        suggested > 0 ? (
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              void act(() =>
                api.POST('/api/admin/tournaments/{tournament_id}/bracket/entrants/confirm-suggestions', {
                  params: { path: { tournament_id: row.id } },
                }),
              )
            }
          >
            Confirm {suggested} suggested
          </Button>
        ) : undefined
      }
    >
      <PanelRow>
        <div className="filter-chips" role="group" aria-label="Show entrants">
          <button type="button" className="filter-chip" aria-pressed={onlyOpen} onClick={() => setOnlyOpen(true)}>
            Need a member · {open.length}
          </button>
          <button type="button" className="filter-chip" aria-pressed={!onlyOpen} onClick={() => setOnlyOpen(false)}>
            All · {data?.entrants.length ?? 0}
          </button>
        </div>
      </PanelRow>
      {error && (
        <PanelRow>
          <Banner tone="error">{error}</Banner>
        </PanelRow>
      )}
      {!data ? (
        <PanelRow>
          <Spinner label="Loading entrants" />
        </PanelRow>
      ) : shown.length === 0 ? (
        <PanelRow>
          <EmptyState icon="check_circle" title={onlyOpen ? 'Every entrant is matched to a member' : 'No entrants yet'}>
            {onlyOpen ? '' : 'Sync the bracket to pull them in.'}
          </EmptyState>
        </PanelRow>
      ) : (
        shown.map((e) => {
          const items: MenuItem[] = [{ label: e.user ? 'Match to someone else…' : 'Match to a member…', icon: 'link', onSelect: () => setMatching(e) }]
          if (e.status === 'suggested')
            items.push({
              label: 'Dismiss suggestion',
              icon: 'close',
              onSelect: () => void act(() => api.POST('/api/admin/tournaments/{tournament_id}/bracket/entrants/{entrant_id}/dismiss', path(e.id))),
            })
          if (e.status === 'manual')
            items.push({
              label: 'Clear hand match',
              icon: 'link_off',
              onSelect: () => void act(() => api.DELETE('/api/admin/tournaments/{tournament_id}/bracket/entrants/{entrant_id}', path(e.id))),
            })
          return (
            <PanelRow key={e.id}>
              <div className="t-row">
                <span className="t-row__text">
                  <span className="t-row__title">{e.name}</span>
                  <span className="t-row__sub">
                    {e.user
                      ? e.user.name
                      : e.suggested_user
                        ? `Looks like ${e.suggested_user.name}`
                        : 'No member yet'}
                    {e.conflicting_user && ` · their Challonge account belongs to ${e.conflicting_user.name}`}
                  </span>
                </span>
                <Chip tone={STATUS[e.status].tone}>{STATUS[e.status].label}</Chip>
                {e.status === 'suggested' && e.suggested_user ? (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      void act(() =>
                        api.PUT('/api/admin/tournaments/{tournament_id}/bracket/entrants/{entrant_id}', {
                          ...path(e.id),
                          body: { user_id: e.suggested_user!.id },
                        }),
                      )
                    }
                  >
                    Confirm
                  </Button>
                ) : null}
                <RowMenu label={`More for ${e.name}`} items={items} />
              </div>
            </PanelRow>
          )
        })
      )}
      {matching && (
        <MatchEntrant
          row={row}
          entrant={matching}
          onClose={() => setMatching(null)}
          onMatched={() => {
            setMatching(null)
            refresh()
          }}
        />
      )}
    </Panel>
  )
}

function MatchEntrant({ row, entrant, onClose, onMatched }: { row: BoardRow; entrant: Entrant; onClose: () => void; onMatched: () => void }) {
  const people = useAdminQuery(`tournaments/${row.id}/people`, () =>
    api.GET('/api/admin/tournaments/{tournament_id}/people', { params: { path: { tournament_id: row.id } } }).then(unwrap),
  )
  const [query, setQuery] = useState(entrant.name)
  const [picked, setPicked] = useState<Person | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const q = query.trim().toLowerCase()
  const matches = (people.data ?? [])
    .filter((p) => !q || p.name.toLowerCase().includes(q) || (p.username ?? '').toLowerCase().includes(q))
    .slice(0, 8)
  async function save() {
    if (!picked) {
      setError('Pick a member first.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      done(
        await api.PUT('/api/admin/tournaments/{tournament_id}/bracket/entrants/{entrant_id}', {
          params: { path: { tournament_id: row.id, entrant_id: entrant.id } },
          body: { user_id: picked.id },
        }),
      )
      onMatched()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Match ${entrant.name}`}
      description="Sync keeps a hand match. Their booked matches move to the member you pick."
      primaryLabel="Match"
      onPrimary={() => void save()}
      busy={busy}
      busyLabel="Matching…"
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <label className="field">
          <span className="field__label">Find a member</span>
          <input className="input" type="search" value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
        <div role="radiogroup" aria-label="Members" className="t-form">
          {matches.map((p) => (
            <label key={p.id} className="t-check">
              <input type="radio" name="entrant-member" checked={picked?.id === p.id} onChange={() => setPicked(p)} />
              {p.name}
            </label>
          ))}
          {people.data && matches.length === 0 && <span className="t-row__sub">No members match.</span>}
        </div>
      </div>
    </FormDialog>
  )
}
