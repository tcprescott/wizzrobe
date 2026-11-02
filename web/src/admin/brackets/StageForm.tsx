import { useEffect, useState } from 'react'
import { api, unwrap, type Schemas } from '../../api/client'
import { Banner, FormDialog, Spinner } from '../../components'
import { message } from '../../me/data'
import { NumberField } from '../kit'
import type { StageRow } from './data'

type Format = 'single_elim' | 'double_elim' | 'swiss' | 'round_robin'
type Config = Record<string, unknown>

interface Props {
  tournaments: Schemas['BracketTournamentResponse'][]
  formats: Schemas['BracketFormatOption'][]
  /** Editing this stage; omit to add one. */
  stage?: StageRow
  onClose: () => void
  onSaved: (stageId: number) => void
}

/** Add a stage, or edit a draft stage's name, format, order and rules. */
export function StageForm({ tournaments, formats, stage, onClose, onSaved }: Props) {
  const existing = stage ? tournaments.find((t) => t.id === stage.tournament_id) : undefined
  const [tournamentId, setTournamentId] = useState<number | ''>(stage?.tournament_id ?? tournaments.find((t) => t.is_active)?.id ?? '')
  const siblings = tournaments.find((t) => t.id === tournamentId)?.stages ?? []
  const [name, setName] = useState(stage?.name ?? '')
  const [format, setFormat] = useState<Format>((stage?.format as Format) ?? 'single_elim')
  const [order, setOrder] = useState<number | null>(stage?.stage_order ?? null)
  const [config, setConfig] = useState<Config | null>(stage ? null : {})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!stage) return
    api
      .GET('/api/brackets/{bracket_id}', { params: { path: { bracket_id: stage.id } } })
      .then(unwrap)
      .then((b) => setConfig((b.config as Config) ?? {}))
      .catch((err) => setError(message(err)))
  }, [stage])

  const stageOrder = order ?? (stage ? stage.stage_order : siblings.length ? Math.max(...siblings.map((s) => s.stage_order)) + 1 : 0)
  const set = (key: string, value: unknown) => setConfig({ ...(config ?? {}), [key]: value })
  const adv = ((config ?? {}).advancement as { count?: number; per_group?: boolean; seeding?: string } | undefined) ?? undefined
  const setAdv = (patch: Partial<{ count: number | null; per_group: boolean; seeding: string }>) => {
    const next = { count: adv?.count ?? 8, per_group: adv?.per_group ?? false, seeding: adv?.seeding ?? 'snake', ...patch }
    set('advancement', next.count ? next : undefined)
  }

  function cleanConfig(): Config {
    const out: Config = {}
    for (const [k, v] of Object.entries(config ?? {})) {
      if (v === null || v === undefined || v === '') continue
      if (k === 'swiss_rounds' && format !== 'swiss') continue
      if (k === 'group_count' && format !== 'round_robin') continue
      if (k === 'grand_final_reset' && format !== 'double_elim') continue
      if (k === 'advancement' && stageOrder === 0) continue
      out[k] = v
    }
    return out
  }

  async function save() {
    if (tournamentId === '') {
      setError('Pick a tournament.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const body = { name: name.trim(), format, stage_order: stageOrder, config: cleanConfig() }
      const saved = stage
        ? await api.PATCH('/api/brackets/{bracket_id}', { params: { path: { bracket_id: stage.id } }, body }).then(unwrap)
        : await api.POST('/api/brackets', { body: { ...body, tournament_id: tournamentId } }).then(unwrap)
      onSaved(saved.id)
      onClose()
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
      title={stage ? `Edit ${stage.name}` : 'Add stage'}
      description={stage ? undefined : 'A stage is one bracket in a tournament. Stages run in order, and a later one can draw the top of the one before.'}
      primaryLabel={stage ? 'Save' : 'Add stage'}
      onPrimary={() => void save()}
      busy={busy}
      busyLabel="Saving…"
    >
      {config === null ? (
        <Spinner label="Loading the stage" />
      ) : (
        <div className="t-form">
          {error && <Banner tone="error">{error}</Banner>}
          {stage ? (
            <p className="t-row__sub" style={{ margin: 0 }}>
              {existing?.name}
            </p>
          ) : (
            <label className="field">
              <span className="field__label">Tournament</span>
              <select className="input" value={tournamentId} onChange={(e) => setTournamentId(e.target.value ? Number(e.target.value) : '')}>
                <option value="">Pick one</option>
                {tournaments.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                    {t.is_active ? '' : ' (inactive)'}
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="t-form__grid">
            <label className="field">
              <span className="field__label">Name</span>
              <input className="input" value={name} placeholder="e.g. Top 8" onChange={(e) => setName(e.target.value)} />
            </label>
            <label className="field">
              <span className="field__label">Format</span>
              <select className="input" value={format} onChange={(e) => setFormat(e.target.value as Format)}>
                {formats.map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field__label">Stage</span>
              <select className="input" value={stageOrder} onChange={(e) => setOrder(Number(e.target.value))}>
                {Array.from({ length: Math.max(siblings.length + 1, stageOrder + 1) }, (_, i) => (
                  <option key={i} value={i}>
                    Stage {i + 1}
                    {i === 0 ? ' (first)' : ''}
                  </option>
                ))}
              </select>
            </label>
            <NumberField label="Games per match (best of)" min={1} value={(config.default_best_of as number) ?? null} onChange={(n) => set('default_best_of', n)} />
            {format === 'swiss' && (
              <NumberField label="Swiss rounds" min={1} value={(config.swiss_rounds as number) ?? null} onChange={(n) => set('swiss_rounds', n)} />
            )}
            {format === 'round_robin' && (
              <NumberField label="Groups" min={1} value={(config.group_count as number) ?? null} onChange={(n) => set('group_count', n)} />
            )}
          </div>
          {format === 'swiss' && <span className="field__hint">Leave Swiss rounds empty to work them out from the field size.</span>}
          {format === 'double_elim' && (
            <label className="t-check">
              <input type="checkbox" checked={config.grand_final_reset !== false} onChange={(e) => set('grand_final_reset', e.target.checked)} />
              Grand final reset when the losers-side player wins
            </label>
          )}
          {stageOrder > 0 && (
            <fieldset className="field t-instant">
              <legend className="field__label">Draw from Stage {stageOrder}</legend>
              <label className="t-check">
                <input type="checkbox" checked={!!adv} onChange={(e) => (e.target.checked ? setAdv({}) : set('advancement', undefined))} />
                Fill this stage from the top of the one before
              </label>
              {adv && (
                <div className="t-form__grid">
                  <NumberField label="How many advance" min={1} value={adv.count ?? null} onChange={(n) => setAdv({ count: n })} />
                  <label className="t-check">
                    <input type="checkbox" checked={!!adv.per_group} onChange={(e) => setAdv({ per_group: e.target.checked })} />
                    Per group
                  </label>
                  {adv.per_group && (
                    <label className="field">
                      <span className="field__label">Seeding</span>
                      <select className="input" value={adv.seeding ?? 'snake'} onChange={(e) => setAdv({ seeding: e.target.value })}>
                        <option value="snake">Snake (keep group-mates apart)</option>
                        <option value="preserve">Rank order</option>
                      </select>
                    </label>
                  )}
                </div>
              )}
            </fieldset>
          )}
        </div>
      )}
    </FormDialog>
  )
}
