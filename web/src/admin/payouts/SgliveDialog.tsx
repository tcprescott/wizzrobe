import { useState } from 'react'
import { api, unwrap } from '../../api/client'
import { Banner, FormDialog, Spinner } from '../../components'
import { done, message } from '../../me/data'
import { useAdminQuery } from '../kit'
import { money } from './data'

/** Pick the tournament each sglive game pays out for. */
export function SgliveDialog({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const links = useAdminQuery('payouts/sglive', () => api.GET('/api/admin/payouts/sglive').then(unwrap))
  const [picks, setPicks] = useState<Record<string, number | null>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const value = (slug: string, fallback: number | null | undefined): number | null => (slug in picks ? picks[slug] ?? null : fallback ?? null)

  async function save() {
    if (!links.data) return
    setBusy(true)
    setError(null)
    try {
      const body = Object.fromEntries(links.data.games.map((g) => [g.slug, value(g.slug, g.tournament_id)]))
      done(await api.PUT('/api/admin/payouts/sglive', { body: { links: body } }))
      onSaved()
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
      title="Link sglive pages"
      description='Pick the tournament each sglive game pays out for. Linked pools update every 15 minutes. Leave one on "Not linked" to type its pool by hand.'
      primaryLabel="Save links"
      busy={busy}
      busyLabel="Saving…"
      onPrimary={() => void save()}
    >
      <div className="t-form">
        {(error || links.error) && <Banner tone="error">{error ?? links.error?.message}</Banner>}
        {!links.data && !links.error && <Spinner label="Reading sglive" />}
        {links.data?.games.length === 0 && <p className="t-row__sub">sglive doesn't list any games right now.</p>}
        {links.data?.games.map((g) => (
          <label key={g.slug} className="field pay-sglive">
            <span className="field__label">
              {g.name} <span className="pay-sglive__total">{money(Math.round(Number(g.total) * 100))} on sglive</span>
            </span>
            <select
              className="input"
              value={value(g.slug, g.tournament_id) ?? ''}
              onChange={(e) => setPicks({ ...picks, [g.slug]: e.target.value ? Number(e.target.value) : null })}
            >
              <option value="">Not linked</option>
              {links.data!.tournaments.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
            {g.suggested && !(g.slug in picks) && <span className="field__hint">Suggested from the name</span>}
          </label>
        ))}
      </div>
    </FormDialog>
  )
}
