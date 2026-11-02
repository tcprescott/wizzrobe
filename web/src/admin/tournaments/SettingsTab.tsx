import { useEffect, useMemo, useState } from 'react'
import { api, unwrap } from '../../api/client'
import { Banner, Button, Panel, PanelRow, Spinner } from '../../components'
import { message } from '../../me/data'
import { useAdminQuery, type BoardRow, type Tournament } from './data'
import { InstantField, NumberField } from './fields'

type Draft = {
  name: string
  short_name: string
  tournament_format: string
  description: string
  seed_generator: string
  preset_id: number | null
  players_per_match: number | null
  average_match_duration: number | null
  max_match_duration: number | null
  signups_open_at: string | null
  signups_close_at: string | null
  rules_url: string
  bracket_url: string
  is_active: boolean
  is_test: boolean
  required_commentators: number | null
  required_trackers: number | null
  required_broadcasters: number | null
}

function toDraft(t: Tournament): Draft {
  return {
    name: t.name,
    short_name: t.short_name ?? '',
    tournament_format: t.tournament_format ?? '',
    description: t.description ?? '',
    seed_generator: t.seed_generator ?? '',
    preset_id: t.preset_id ?? null,
    players_per_match: t.players_per_match,
    average_match_duration: t.average_match_duration ?? null,
    max_match_duration: t.max_match_duration ?? null,
    signups_open_at: t.signups_open_at ?? null,
    signups_close_at: t.signups_close_at ?? null,
    rules_url: t.rules_url ?? '',
    bracket_url: t.bracket_url ?? '',
    is_active: t.is_active,
    is_test: t.is_test,
    required_commentators: t.required_commentators,
    required_trackers: t.required_trackers,
    required_broadcasters: t.required_broadcasters ?? 0,
  }
}

const NULLABLE_TEXT = new Set(['short_name', 'tournament_format', 'description', 'seed_generator', 'rules_url', 'bracket_url'])

/** The fields that changed, blank text sent as null so the server clears it. */
function changes(before: Draft, after: Draft): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const key of Object.keys(after) as (keyof Draft)[]) {
    if (after[key] === before[key]) continue
    const value = after[key]
    out[key] = NULLABLE_TEXT.has(key) && value === '' ? null : value
  }
  return out
}

/** Everything about the tournament itself: name, seeds, match length, signups, links, crew. */
export function SettingsTab({ row, zone, onSaved }: { row: BoardRow; zone: string | null; onSaved: () => void }) {
  const tournament = useAdminQuery(`tournament/${row.id}`, () =>
    api.GET('/api/tournaments/{tournament_id}', { params: { path: { tournament_id: row.id } } }).then(unwrap),
  )
  const randomizers = useAdminQuery('seeds/randomizers', () => api.GET('/api/seeds/randomizers').then(unwrap))
  const presets = useAdminQuery('presets/selectable', () => api.GET('/api/presets/selectable').then(unwrap))
  const original = useMemo(() => (tournament.data ? toDraft(tournament.data) : null), [tournament.data])
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => setDraft(original), [original])

  if (tournament.error && !tournament.data) {
    return (
      <Banner tone="error" action={<Button variant="outline" onClick={tournament.reload}>Try again</Button>}>
        {tournament.error.message}
      </Banner>
    )
  }
  if (!draft || !original) return <Spinner label="Loading settings" />

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setSaved(false)
    setDraft({ ...draft, [key]: value })
  }
  const diff = changes(original, draft)
  const dirty = Object.keys(diff).length > 0
  const randomizerOptions = randomizers.data?.map((r) => r.randomizer) ?? []
  if (draft.seed_generator && !randomizerOptions.includes(draft.seed_generator)) randomizerOptions.unshift(draft.seed_generator)
  const presetOptions = (presets.data ?? []).filter((p) => !draft.seed_generator || p.randomizer === draft.seed_generator)

  async function save() {
    setBusy(true)
    setError(null)
    try {
      await api.PATCH('/api/tournaments/{tournament_id}', { params: { path: { tournament_id: row.id } }, body: diff }).then(unwrap)
      setSaved(true)
      tournament.reload()
      onSaved()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  const text = (key: keyof Draft, label: string, props: { placeholder?: string; type?: string; hint?: string } = {}) => (
    <label className="field">
      <span className="field__label">{label}</span>
      <input
        className="input"
        type={props.type ?? 'text'}
        value={draft[key] as string}
        placeholder={props.placeholder}
        onChange={(e) => set(key, e.target.value as never)}
      />
      {props.hint && <span className="field__hint">{props.hint}</span>}
    </label>
  )
  const check = (key: 'is_active' | 'is_test', label: string) => (
    <label className="t-check">
      <input type="checkbox" checked={draft[key]} onChange={(e) => set(key, e.target.checked)} />
      {label}
    </label>
  )

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
      <Panel title="Basics">
        <PanelRow>
          <div className="t-form">
            <div className="t-form__grid">
              {text('name', 'Name')}
              {text('short_name', 'Short name', { hint: 'Used where space is tight, like run sheet rows.' })}
              {text('tournament_format', 'Format', { placeholder: 'e.g. Double elimination' })}
            </div>
            <label className="field">
              <span className="field__label">Description</span>
              <textarea className="input" rows={4} value={draft.description} onChange={(e) => set('description', e.target.value)} />
              <span className="field__hint">Shown to players. Markdown works.</span>
            </label>
            {check('is_active', 'Active (players can see it and book matches)')}
            {check('is_test', 'Test tournament (hidden from the event info page)')}
          </div>
        </PanelRow>
      </Panel>
      <Panel title="Seeds and matches">
        <PanelRow>
          <div className="t-form__grid">
            <label className="field">
              <span className="field__label">Randomizer</span>
              <select className="input" value={draft.seed_generator} onChange={(e) => setDraft({ ...draft, seed_generator: e.target.value, preset_id: null })}>
                <option value="">No seeds</option>
                {randomizerOptions.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field__label">Preset</span>
              <select
                className="input"
                value={draft.preset_id ?? ''}
                disabled={!draft.seed_generator}
                onChange={(e) => set('preset_id', e.target.value ? Number(e.target.value) : null)}
              >
                <option value="">Randomizer default</option>
                {presetOptions.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            <NumberField label="Players per match" min={1} value={draft.players_per_match} onChange={(n) => set('players_per_match', n)} />
            <NumberField label="Typical match (minutes)" value={draft.average_match_duration} onChange={(n) => set('average_match_duration', n)} />
            <NumberField label="Longest match (minutes)" value={draft.max_match_duration} onChange={(n) => set('max_match_duration', n)} />
          </div>
        </PanelRow>
      </Panel>
      <Panel title="Signups">
        <PanelRow>
          <div className="t-form__grid">
            <InstantField label="Signups open" value={draft.signups_open_at} zone={zone} onChange={(v) => set('signups_open_at', v)} hint="Leave empty to open now." />
            <InstantField label="Signups close" value={draft.signups_close_at} zone={zone} onChange={(v) => set('signups_close_at', v)} hint="Leave empty to never close." />
          </div>
        </PanelRow>
      </Panel>
      <Panel title="Links">
        <PanelRow>
          <div className="t-form__grid">
            {text('rules_url', 'Rules link', { type: 'url', placeholder: 'https://' })}
            {text('bracket_url', 'Bracket link', { type: 'url', placeholder: 'https://', hint: 'For a bracket not linked on the Bracket tab.' })}
          </div>
        </PanelRow>
      </Panel>
      <Panel title="Crew per match">
        <PanelRow>
          <div className="t-form__grid">
            <NumberField label="Commentators" value={draft.required_commentators} onChange={(n) => set('required_commentators', n)} />
            <NumberField label="Trackers" value={draft.required_trackers} onChange={(n) => set('required_trackers', n)} />
            <NumberField label="Broadcasters" value={draft.required_broadcasters} onChange={(n) => set('required_broadcasters', n)} />
          </div>
        </PanelRow>
      </Panel>
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
