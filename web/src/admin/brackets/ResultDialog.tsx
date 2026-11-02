import { useState } from 'react'
import { api, unwrap } from '../../api/client'
import { Banner, FormDialog } from '../../components'
import { message } from '../../me/data'
import { NumberField } from '../kit'
import type { StageMatch } from './data'

/** Report an open match, or correct a reported one: winner, optional scores, forfeit. */
export function ResultDialog({ match: m, onClose, onSaved }: { match: StageMatch; onClose: () => void; onSaved: (text: string) => void }) {
  const correcting = m.state === 'complete'
  const [winner, setWinner] = useState<number | null>(m.winner_entry_id ?? null)
  const [s1, setS1] = useState<number | null>(m.side1.score ?? null)
  const [s2, setS2] = useState<number | null>(m.side2.score ?? null)
  const [forfeit, setForfeit] = useState(m.forfeit)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const sides = [m.side1, m.side2].filter((s) => s.entry_id != null)

  async function save() {
    if (winner == null) {
      setError('Pick who won.')
      return
    }
    if ((s1 == null) !== (s2 == null)) {
      setError('Enter both scores, or neither.')
      return
    }
    setBusy(true)
    setError(null)
    const body = { winner_entry_id: winner, entry1_score: s1, entry2_score: s2, forfeit }
    const params = { path: { match_id: m.id } }
    try {
      if (correcting) await api.PATCH('/api/brackets/matches/{match_id}/result', { params, body }).then(unwrap)
      else await api.POST('/api/brackets/matches/{match_id}/result', { params, body }).then(unwrap)
      const name = winner === m.side1.entry_id ? m.side1.name : m.side2.name
      onSaved(correcting ? `${m.label} corrected: ${name} won.` : `${name} won ${m.label}.`)
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
      title={correcting ? `Correct ${m.label}` : `Report ${m.label}`}
      description={`${m.side1.name} vs ${m.side2.name}`}
      primaryLabel={correcting ? 'Save correction' : 'Report result'}
      onPrimary={() => void save()}
      busy={busy}
      busyLabel="Saving…"
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        {correcting && <Banner tone="info">Correcting moves the new winner on. It only works while the matches after this one haven't been played.</Banner>}
        <fieldset className="field">
          <legend className="field__label">Winner</legend>
          {sides.map((s) => (
            <label key={s.entry_id} className="t-check">
              <input type="radio" name="winner" checked={winner === s.entry_id} onChange={() => setWinner(s.entry_id ?? null)} />
              {s.name}
            </label>
          ))}
        </fieldset>
        <div className="t-form__grid">
          <NumberField label={`${m.side1.name} score`} value={s1} onChange={setS1} />
          <NumberField label={`${m.side2.name} score`} value={s2} onChange={setS2} />
        </div>
        <label className="t-check">
          <input type="checkbox" checked={forfeit} onChange={(e) => setForfeit(e.target.checked)} />
          Forfeit, disqualification or walkover
        </label>
      </div>
    </FormDialog>
  )
}
