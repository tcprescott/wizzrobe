import { useState } from 'react'
import { api, unwrap } from '../../api/client'
import { Banner, Button, Chip, FormDialog, IconButton, Panel, PanelRow, Spinner } from '../../components'
import { effectiveZone, formatDayTime } from '../../lib/time'
import { usePhone } from '../../lib/usePhone'
import { done, message } from '../../me/data'
import { useAdminQuery, useDraft, useUnsavedFlag } from '../kit'
import {
  allocation,
  draftFrom,
  lineCents,
  money,
  ordinal,
  parseNumber,
  splitBody,
  STATUS_TONE,
  totalCents,
  type Draft,
  type DraftLine,
  type PayoutRow,
  type Unit,
} from './data'

interface Props {
  row: PayoutRow
  zone: string | null
  onSaved: () => void
}

let nextKey = 0

/** One tournament's pool and split, edited in place, saved together from the sticky bar. */
export function PayoutEditor({ row, zone, onSaved }: Props) {
  const Heading = usePhone() ? 'h1' : 'h2'
  const detail = useAdminQuery(`payouts/${row.id}`, () =>
    api.GET('/api/admin/payouts/{tournament_id}', { params: { path: { tournament_id: row.id } } }).then(unwrap),
  )
  const saved = detail.data ? draftFrom(detail.data) : undefined
  const draft = useDraft<Draft>(saved)
  useUnsavedFlag(draft.dirty)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)
  const [unlinking, setUnlinking] = useState(false)

  if (detail.error && !detail.data) {
    return (
      <Banner tone="error" action={<Button variant="outline" onClick={detail.reload}>Try again</Button>}>
        {detail.error.message}
      </Banner>
    )
  }
  if (!detail.data || !draft.value) return <Spinner label="Loading the split" />
  const d = detail.data
  const value = draft.value
  const synced = !!d.sglive_slug
  const total = totalCents(value)
  const alloc = allocation(value)

  const setLine = (key: string, part: Partial<DraftLine>) =>
    draft.patch({ lines: value.lines.map((l) => (l.key === key ? { ...l, ...part } : l)) })

  function addPlace() {
    const last = value.lines.reduce((max, l) => Math.max(max, Number(l.place) || 0), 0)
    nextKey += 1
    draft.patch({
      lines: [...value.lines, { key: `new-${nextKey}`, place: String(last + 1), unit: '%', share: '', entrant_id: null, note: null }],
    })
  }

  async function copyBlock() {
    setNote(null)
    try {
      const r = await api.GET('/api/admin/payouts/{tournament_id}/export', { params: { path: { tournament_id: d.id } } }).then(unwrap)
      await navigator.clipboard.writeText(r.text)
      setNote({ tone: 'info', text: draft.dirty ? 'Copied the saved split. Save first to copy your changes.' : 'Copied. Paste it into the Matcherino thread.' })
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    }
  }

  async function save() {
    const body = splitBody(value)
    if (typeof body === 'string') {
      setNote({ tone: 'error', text: body })
      return
    }
    setBusy(true)
    setNote(null)
    try {
      const path = { params: { path: { tournament_id: d.id } } }
      const poolChanged = saved && (value.pool !== saved.pool || value.bonus !== saved.bonus)
      if (poolChanged) {
        const pool = parseNumber(value.pool)
        const bonus = parseNumber(value.bonus)
        if ((value.pool.trim() && pool === null) || (value.bonus.trim() && bonus === null)) {
          throw new Error('The pool and the bonus must be numbers, or blank.')
        }
        await api
          .PUT('/api/tournaments/{tournament_id}/prize-pool', {
            ...path,
            body: { prize_pool: synced ? d.pool : pool === null ? null : pool.toFixed(2), prize_bonus: bonus === null ? null : bonus.toFixed(2) },
          })
          .then(unwrap)
      }
      await api.PUT('/api/tournaments/{tournament_id}/payouts', { ...path, body }).then(unwrap)
      draft.discard()
      detail.reload()
      onSaved()
      setNote({ tone: 'info', text: 'Saved.' })
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    } finally {
      setBusy(false)
    }
  }

  const left = alloc.total - alloc.placed
  const summary = [
    [alloc.percent ? `${+alloc.percent.toFixed(2)}%` : null, alloc.fixed ? money(alloc.fixed) : null].filter(Boolean).join(' + ') || 'Nothing placed',
    left > 0 ? `${money(left)} left to place` : left < 0 ? `${money(-left)} over the total` : alloc.total ? 'All placed' : null,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <div className="pay-editor">
      <div className="detail-head">
        <div className="detail-head__text">
          <Heading>{d.name}</Heading>
          <div className="detail-head__sub">
            <Chip tone={STATUS_TONE[d.status] ?? 'neutral'}>{d.status_label}</Chip>
            {draft.dirty && <Chip tone="pending">Unsaved changes</Chip>}
          </div>
        </div>
        <Button variant="outline" icon="content_copy" onClick={() => void copyBlock()} disabled={d.lines.length === 0}>
          Copy for Matcherino
        </Button>
      </div>

      {note && (
        <Banner tone={note.tone} action={<Button variant="ghost" onClick={() => setNote(null)}>Dismiss</Button>}>
          {note.text}
        </Banner>
      )}

      <Panel title="Prize pool">
        <PanelRow>
          <div className="pay-pool">
            <label className="field">
              <span className="field__label">Pool</span>
              <input
                className="input"
                inputMode="decimal"
                value={value.pool}
                placeholder="Not set"
                disabled={synced}
                onChange={(e) => draft.patch({ pool: e.target.value })}
              />
              {synced && (
                <span className="field__hint pay-pool__sync">
                  {d.synced_at ? `Synced from sglive ${formatDayTime(d.synced_at, effectiveZone(zone))}` : 'Syncs from sglive'}
                  <Button size="sm" variant="ghost" icon="link_off" onClick={() => setUnlinking(true)}>
                    Unlink
                  </Button>
                </span>
              )}
            </label>
            <label className="field">
              <span className="field__label">Bonus</span>
              <input className="input" inputMode="decimal" value={value.bonus} placeholder="None" onChange={(e) => draft.patch({ bonus: e.target.value })} />
            </label>
            <div className="field">
              <span className="field__label">Total</span>
              <span className="pay-money pay-pool__total">{money(total)}</span>
            </div>
          </div>
        </PanelRow>
      </Panel>

      <Panel title="Split">
        {value.lines.length === 0 && (
          <PanelRow>
            <span className="t-row__sub">No places yet. Add one for each place that wins something.</span>
          </PanelRow>
        )}
        {value.lines.map((line) => (
          <PanelRow key={line.key}>
            <SplitLine
              line={line}
              total={total}
              entrants={d.entrants}
              onChange={(part) => setLine(line.key, part)}
              onRemove={() => draft.patch({ lines: value.lines.filter((l) => l.key !== line.key) })}
            />
          </PanelRow>
        ))}
        <PanelRow>
          <Button variant="ghost" icon="add" onClick={addPlace}>
            Add place
          </Button>
        </PanelRow>
      </Panel>

      <div className="pay-bar" role="region" aria-label="Save the split">
        <div className="pay-bar__text" aria-live="polite">
          <strong className={left < 0 ? 'pay-bar__over' : undefined}>
            {money(alloc.placed)} of {money(alloc.total)}
          </strong>
          <span className="t-row__sub">{summary}</span>
        </div>
        <Button variant="outline" icon="undo" disabled={!draft.dirty || busy} onClick={() => { draft.discard(); setNote(null) }}>
          Undo
        </Button>
        <Button disabled={!draft.dirty} busy={busy} busyLabel="Saving…" onClick={() => void save()}>
          Save
        </Button>
      </div>

      {unlinking && (
        <FormDialog
          open
          size="sm"
          onOpenChange={(o) => !o && setUnlinking(false)}
          title="Unlink from sglive?"
          description="The pool stops updating from sglive. It keeps its last amount, and you can type it by hand."
          primaryLabel="Unlink"
          onPrimary={() => {
            void api
              .DELETE('/api/admin/payouts/{tournament_id}/sglive', { params: { path: { tournament_id: d.id } } })
              .then(done)
              .then(() => {
                setUnlinking(false)
                detail.reload()
                onSaved()
              })
              .catch((err) => {
                setUnlinking(false)
                setNote({ tone: 'error', text: message(err) })
              })
          }}
        >
          <span />
        </FormDialog>
      )}
    </div>
  )
}

function SplitLine({
  line,
  total,
  entrants,
  onChange,
  onRemove,
}: {
  line: DraftLine
  total: number
  entrants: { id: number; name: string }[]
  onChange: (part: Partial<DraftLine>) => void
  onRemove: () => void
}) {
  const cents = lineCents(line, total)
  const place = Number(line.place)
  const label = Number.isInteger(place) && place > 0 ? `${ordinal(place)} place` : 'This place'
  const known = line.entrant_id == null || entrants.some((e) => e.id === line.entrant_id)
  return (
    <div className="pay-line">
      <input
        className="input pay-line__place"
        type="number"
        min={1}
        inputMode="numeric"
        aria-label="Place"
        value={line.place}
        onChange={(e) => onChange({ place: e.target.value })}
      />
      <select
        className="input pay-line__who"
        aria-label={`Winner of ${label}`}
        value={line.entrant_id ?? ''}
        onChange={(e) => onChange({ entrant_id: e.target.value ? Number(e.target.value) : null })}
      >
        <option value="">Not decided yet</option>
        {!known && <option value={line.entrant_id!}>Someone no longer playing</option>}
        {entrants.map((e) => (
          <option key={e.id} value={e.id}>
            {e.name}
          </option>
        ))}
      </select>
      <input
        className="input pay-line__share"
        inputMode="decimal"
        aria-label={`${label} share, ${line.unit === '%' ? 'percent of the total' : 'dollars'}`}
        placeholder={line.unit === '%' ? '%' : '$'}
        value={line.share}
        onChange={(e) => onChange({ share: e.target.value })}
      />
      <div className="segmented pay-line__unit" role="radiogroup" aria-label={`${label} paid as`}>
        {(['$', '%'] as Unit[]).map((u) => (
          <span key={u}>
            <input
              type="radio"
              id={`unit-${line.key}-${u}`}
              name={`unit-${line.key}`}
              checked={line.unit === u}
              onChange={() => onChange({ unit: u })}
            />
            <label htmlFor={`unit-${line.key}-${u}`} aria-label={u === '$' ? 'Dollars' : 'Percent'}>
              {u}
            </label>
          </span>
        ))}
      </div>
      <span className="pay-money pay-line__amount">{cents === null ? '—' : money(cents)}</span>
      <span className="pay-line__del">
        <IconButton icon="delete" label={`Remove ${label}`} onClick={onRemove} />
      </span>
    </div>
  )
}
