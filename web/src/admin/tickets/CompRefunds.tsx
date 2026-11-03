import { useState } from 'react'
import { api, unwrap } from '../../api/client'
import { Banner, Button, CountBadge, FormDialog, Panel } from '../../components'
import { useAction } from '../peopleKit'
import { money, providerName, type CompRow } from './data'

function sub(r: CompRow): string {
  const parts = [r.reasons.join(', ')]
  if (r.scheduled_hours != null) parts.push(`${Math.round(r.scheduled_hours * 10) / 10} h scheduled`)
  return parts.filter(Boolean).join(' · ')
}

const total = (rows: CompRow[]) => rows.reduce((sum, r) => sum + r.amount_cents, 0)

/** Comped people who paid for their own badge: tick some (or one) and refund them, after a confirm. */
export function CompRefundsPanel({ rows, onDone }: { rows: CompRow[]; onDone: (note: { tone: 'info' | 'warning'; text: string }) => void }) {
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [confirming, setConfirming] = useState<CompRow[] | null>(null)
  const { busy, error, setError, run } = useAction()
  const selected = rows.filter((r) => picked.has(r.ticket_id))
  const currency = rows[0]?.currency ?? 'usd'

  function toggle(id: number, on: boolean) {
    const next = new Set(picked)
    if (on) next.add(id)
    else next.delete(id)
    setPicked(next)
  }

  async function refund(list: CompRow[]) {
    let results: { ticket_id: number; ok: boolean; error?: string | null }[] = []
    const ok = await run('refund', async () => {
      results = await api.POST('/api/tickets/comp-refunds', { body: { ticket_ids: list.map((r) => r.ticket_id) } }).then(unwrap)
    })
    if (!ok) return
    setConfirming(null)
    setPicked(new Set())
    const failed = results.filter((r) => !r.ok)
    const done = results.length - failed.length
    const name = (id: number) => rows.find((r) => r.ticket_id === id)?.user_name ?? 'someone'
    onDone(
      failed.length
        ? {
            tone: 'warning',
            text: `${done ? `Refunded ${done}. ` : ''}Couldn't refund ${failed.map((f) => `${name(f.ticket_id)} (${f.error ?? 'the store said no'})`).join(', ')}.`,
          }
        : { tone: 'info', text: `Refunded ${done} comped ${done === 1 ? 'badge' : 'badges'}. Each person gets a DM once the store confirms.` },
    )
  }

  const one = confirming?.length === 1
  return (
    <Panel
      title={
        <>
          Comp refunds <CountBadge count={rows.length} label={`${rows.length} owed`} />
        </>
      }
      actions={<span className="tk-panel-note">Comped people who paid for their own badge</span>}
    >
      <ul className="tk-list" aria-label="Comp refunds">
        {rows.map((r) => (
          <li key={r.ticket_id} className="panel__row tk-row">
            <input
              type="checkbox"
              className="tk-check"
              aria-label={`Select ${r.user_name}`}
              checked={picked.has(r.ticket_id)}
              onChange={(e) => toggle(r.ticket_id, e.target.checked)}
            />
            <span className="tk-row__text">
              <span className="tk-row__title">
                <strong>{r.user_name}</strong> · {r.ticket_type_name} · paid {money(r.paid_cents, r.currency)}
              </span>
              <span className="tk-row__sub">{sub(r)}</span>
            </span>
            <span className="tk-row__amount">{money(r.amount_cents, r.currency)}</span>
            <Button variant="outline" size="sm" aria-label={`Refund ${r.user_name}`} onClick={() => setConfirming([r])}>
              Refund
            </Button>
          </li>
        ))}
      </ul>
      <div className="tk-foot">
        <span className="tk-foot__sum">
          {selected.length} selected · {money(total(selected), currency)}
        </span>
        <Button icon="undo" disabled={selected.length === 0} onClick={() => setConfirming(selected)}>
          Refund selected
        </Button>
      </div>

      <FormDialog
        open={confirming != null}
        onOpenChange={(o) => {
          if (!o) {
            setConfirming(null)
            setError(null)
          }
        }}
        title={one ? `Refund ${confirming?.[0]?.user_name}?` : `Refund ${confirming?.length ?? 0} comped badges?`}
        size="sm"
        primaryLabel={`Refund ${money(total(confirming ?? []), currency)}`}
        destructive
        busy={busy === 'refund'}
        busyLabel="Refunding…"
        onPrimary={() => confirming && void refund(confirming)}
      >
        <ul className="tk-confirm">
          {(confirming ?? []).map((r) => (
            <li key={r.ticket_id}>
              <span>
                <strong>{r.user_name}</strong> · {money(r.amount_cents, r.currency)}
              </span>
              <span className="tk-row__sub">Back to their {providerName(r.provider)} payment</span>
            </li>
          ))}
        </ul>
        <p>{one ? 'Their badge stays valid, and they get a DM' : 'Their badges stay valid. Each gets a DM'} once the store confirms. This can't be undone.</p>
        {error && <Banner tone="error">{error}</Banner>}
      </FormDialog>
    </Panel>
  )
}
