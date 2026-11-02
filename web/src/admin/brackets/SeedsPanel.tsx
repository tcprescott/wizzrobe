import { useState } from 'react'
import { api, unwrap } from '../../api/client'
import { Banner, Button, FormDialog, Panel, PanelRow, RowMenu, Spinner } from '../../components'
import { message } from '../../me/data'
import { useAdminQuery } from '../kit'
import type { SeedEntry, StageDetail } from './data'

type Note = { tone: 'info' | 'error'; text: string } | null

interface PanelProps {
  stage: StageDetail
  onChange: () => void
  onNote: (note: Note) => void
}

/** A draft stage's enrolled entries in seed order, with the seed tools. */
export function SeedsPanel({ stage, onChange, onNote }: PanelProps) {
  const [fromQualifier, setFromQualifier] = useState(false)
  const bracketId = stage.row.id
  const entries = stage.entries

  async function act(call: () => Promise<unknown>, text: string) {
    onNote(null)
    try {
      await call()
      onNote({ tone: 'info', text })
      onChange()
    } catch (err) {
      onNote({ tone: 'error', text: message(err) })
    }
  }

  const setSeeds = (order: SeedEntry[] | null, text: string) =>
    act(
      () =>
        api
          .PATCH('/api/brackets/{bracket_id}/seeds', {
            params: { path: { bracket_id: bracketId } },
            body: { seeds: Object.fromEntries(entries.map((e) => [e.id, order ? order.indexOf(e) + 1 : null])) },
          })
          .then(unwrap),
      text,
    )

  const seeded = [...entries].sort((a, b) => (a.seed ?? 1e9) - (b.seed ?? 1e9))
  const shuffled = () => {
    const out = [...entries]
    for (let i = out.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1))
      const t = out[i]!
      out[i] = out[j]!
      out[j] = t
    }
    return out
  }

  const move = (entry: SeedEntry, by: number) => {
    const order = [...seeded]
    const i = order.indexOf(entry)
    const j = i + by
    if (j < 0 || j >= order.length) return
    order.splice(i, 1)
    order.splice(j, 0, entry)
    void setSeeds(order, `${entry.name} is now seed ${j + 1}.`)
  }

  return (
    <Panel
      title={`Seeds · ${entries.length}`}
      actions={
        entries.length > 1 && (
          <RowMenu
            label="Seed tools"
            items={[
              { label: 'Number them 1 to N', icon: 'format_list_numbered', onSelect: () => void setSeeds(seeded, 'Seeds numbered 1 to N in the current order.') },
              { label: 'Reverse', icon: 'swap_vert', onSelect: () => void setSeeds([...seeded].reverse(), 'Seed order reversed.') },
              { label: 'Shuffle', icon: 'shuffle', onSelect: () => void setSeeds(shuffled(), 'Seeds shuffled.') },
              { label: 'From a qualifier…', icon: 'leaderboard', onSelect: () => setFromQualifier(true) },
              { label: 'Clear seeds', icon: 'close', destructive: true, onSelect: () => void setSeeds(null, 'Seeds cleared.') },
            ]}
          />
        )
      }
    >
      {entries.length === 0 && (
        <PanelRow>
          <span className="t-row__sub">Nobody's enrolled yet. Enroll from the roster below.</span>
        </PanelRow>
      )}
      {seeded.map((e, i) => (
        <PanelRow key={e.id}>
          <div className="t-row">
            <span className="seed-no" aria-label={e.seed ? `Seed ${e.seed}` : 'No seed'}>
              {e.seed ?? '–'}
            </span>
            <span className="t-row__text">
              <span className="t-row__title">{e.name}</span>
              {e.group_number != null && <span className="t-row__sub">Group {e.group_number}</span>}
            </span>
            <RowMenu
              label={`Actions for ${e.name}`}
              items={[
                { label: 'Move up', icon: 'arrow_upward', disabled: i === 0, onSelect: () => move(e, -1) },
                { label: 'Move down', icon: 'arrow_downward', disabled: i === seeded.length - 1, onSelect: () => move(e, 1) },
                {
                  label: 'Unenroll',
                  icon: 'person_remove',
                  destructive: true,
                  onSelect: () =>
                    void act(
                      () => api.DELETE('/api/brackets/entries/{entry_id}', { params: { path: { entry_id: e.id } } }).then(unwrap),
                      `${e.name} is out of this stage.`,
                    ),
                },
              ]}
            />
          </div>
        </PanelRow>
      ))}
      {fromQualifier && (
        <QualifierSeedsDialog
          bracketId={bracketId}
          onClose={() => setFromQualifier(false)}
          onApplied={(text) => {
            setFromQualifier(false)
            onNote({ tone: 'info', text })
            onChange()
          }}
        />
      )}
    </Panel>
  )
}

function QualifierSeedsDialog({ bracketId, onClose, onApplied }: { bracketId: number; onClose: () => void; onApplied: (text: string) => void }) {
  const list = useAdminQuery('brackets/qualifiers', () => api.GET('/api/async-qualifiers').then(unwrap))
  const [qualifierId, setQualifierId] = useState<number | ''>('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function apply() {
    if (qualifierId === '') {
      setError('Pick a qualifier.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const seeding = await api
        .GET('/api/admin/brackets/{bracket_id}/qualifier-seeds', { params: { path: { bracket_id: bracketId }, query: { qualifier_id: qualifierId } } })
        .then(unwrap)
      await api
        .PATCH('/api/brackets/{bracket_id}/seeds', {
          params: { path: { bracket_id: bracketId } },
          body: { seeds: Object.fromEntries(Object.entries(seeding.seeds).map(([k, v]) => [Number(k), v])) },
        })
        .then(unwrap)
      const n = Object.keys(seeding.seeds).length
      const missing = seeding.unmatched.length ? ` Not on the leaderboard: ${seeding.unmatched.join(', ')}.` : ''
      onApplied(`Seeded ${n} from the leaderboard.${missing}`)
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
      title="Seed from a qualifier"
      description="Best leaderboard place gets seed 1. Entrants match by their linked account."
      primaryLabel="Apply seeds"
      onPrimary={() => void apply()}
      busy={busy}
      busyLabel="Seeding…"
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        {!list.data ? (
          list.error ? <Banner tone="error">{list.error.message}</Banner> : <Spinner label="Loading qualifiers" />
        ) : list.data.length === 0 ? (
          <span className="t-row__sub">This event has no qualifiers.</span>
        ) : (
          <label className="field">
            <span className="field__label">Qualifier</span>
            <select className="input" value={qualifierId} onChange={(e) => setQualifierId(e.target.value ? Number(e.target.value) : '')}>
              <option value="">Pick one</option>
              {list.data.map((q) => (
                <option key={q.id} value={q.id}>
                  {q.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
    </FormDialog>
  )
}

/** The tournament roster: who could be in this stage, and who isn't yet. */
export function RosterPanel({ stage, onChange, onNote }: PanelProps) {
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState(false)
  const bracketId = stage.row.id
  const tournamentId = stage.row.tournament_id

  async function act(call: () => Promise<unknown>, text: string) {
    setBusy(true)
    onNote(null)
    try {
      await call()
      onNote({ tone: 'info', text })
      onChange()
    } catch (err) {
      onNote({ tone: 'error', text: message(err) })
    } finally {
      setBusy(false)
    }
  }

  const waiting = stage.roster.filter((r) => !r.enrolled)
  return (
    <Panel
      title={`Roster · ${stage.roster.length}`}
      actions={
        <RowMenu
          label="Roster actions"
          items={[
            { label: 'Add entrant…', icon: 'person_add', onSelect: () => setAdding(true) },
            {
              label: "Import the tournament's players",
              icon: 'group_add',
              onSelect: () =>
                void act(
                  () => api.POST('/api/brackets/entrants/import', { body: { tournament_id: tournamentId } }).then(unwrap),
                  "Imported the tournament's players onto the roster.",
                ),
            },
          ]}
        />
      }
    >
      {stage.roster.length === 0 && (
        <PanelRow>
          <span className="t-row__sub">The roster is empty. Import the tournament's players, or add entrants by name.</span>
        </PanelRow>
      )}
      {waiting.map((r) => (
        <PanelRow key={r.id}>
          <div className="t-row">
            <span className="t-row__text">
              <span className="t-row__title">{r.name}</span>
              <span className="t-row__sub">{r.status === 'dropped' ? 'Dropped' : r.user_id ? 'Not in this stage' : 'Not in this stage · no linked account'}</span>
            </span>
            {r.status !== 'dropped' && (
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() =>
                  void act(
                    () => api.POST('/api/brackets/{bracket_id}/entries', { params: { path: { bracket_id: bracketId } }, body: { entrant_id: r.id } }).then(unwrap),
                    `${r.name} is in.`,
                  )
                }
              >
                Enroll
              </Button>
            )}
            {r.status !== 'dropped' && (
              <RowMenu
                label={`Actions for ${r.name}`}
                items={[
                  {
                    label: 'Drop from the tournament',
                    icon: 'person_remove',
                    destructive: true,
                    onSelect: () =>
                      void act(
                        () => api.POST('/api/brackets/entrants/{entrant_id}/drop', { params: { path: { entrant_id: r.id } } }).then(unwrap),
                        `${r.name} is dropped from the roster.`,
                      ),
                  },
                ]}
              />
            )}
          </div>
        </PanelRow>
      ))}
      {waiting.length === 0 && stage.roster.length > 0 && (
        <PanelRow>
          <span className="t-row__sub">Everyone on the roster is enrolled.</span>
        </PanelRow>
      )}
      {adding && (
        <AddEntrantDialog
          tournamentId={tournamentId}
          onClose={() => setAdding(false)}
          onAdded={(text) => {
            setAdding(false)
            onNote({ tone: 'info', text })
            onChange()
          }}
        />
      )}
    </Panel>
  )
}

function AddEntrantDialog({ tournamentId, onClose, onAdded }: { tournamentId: number; onClose: () => void; onAdded: (text: string) => void }) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function add() {
    if (!name.trim()) {
      setError('Enter a name.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await api.POST('/api/brackets/entrants', { body: { tournament_id: tournamentId, display_name: name.trim() } }).then(unwrap)
      onAdded(`${name.trim()} is on the roster.`)
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <FormDialog
      open
      size="sm"
      onOpenChange={(o) => !o && onClose()}
      title="Add entrant"
      description="A name for the bracket. Link it to an account later on the website if you need DMs to reach them."
      primaryLabel="Add"
      onPrimary={() => void add()}
      busy={busy}
      busyLabel="Adding…"
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <label className="field">
          <span className="field__label">Name</span>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
      </div>
    </FormDialog>
  )
}
