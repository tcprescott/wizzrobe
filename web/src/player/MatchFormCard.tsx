import { useEffect, useId, useRef, useState } from 'react'
import { api, unwrap, type Schemas } from '../api/client'
import { useQuery } from '../api/useQuery'
import { Banner, Button, Icon, Spinner } from '../components'
import { message } from '../me/data'

type Form = Schemas['PlayerFormResponse']
type Question = Form['questions'][number]
type Value = string | number | string[] | null

function seesLine(q: Question, others: string): string {
  if (q.visibility === 'never') return `${others} won't see your answer.`
  if (q.visibility === 'at_roll') return `${others} sees it when the seed rolls.`
  return `${others} sees your answer.`
}

/**
 * "Before your match": the questions this match's form asks the player, as on
 * the DM's link. Renders nothing when there's nothing to answer, unless
 * `explain` (the link named this match) asks it to say so.
 */
export function MatchFormCard({ matchId, explain = false, focus = false }: { matchId: number; explain?: boolean; focus?: boolean }) {
  const form = useQuery(`match-form/${matchId}`, () =>
    api.GET('/api/matches/{match_id}/form', { params: { path: { match_id: matchId } } }).then(unwrap),
  )
  const titleId = useId()
  const heading = useRef<HTMLHeadingElement>(null)
  const [draft, setDraft] = useState<Record<string, Value>>({})
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)

  useEffect(() => {
    if (!form.data) return
    setDraft(Object.fromEntries(form.data.questions.map((q) => [q.key, (q.answer ?? null) as Value])))
  }, [form.data])
  useEffect(() => {
    if (focus && form.data?.questions.length) heading.current?.focus()
  }, [focus, form.data])

  if (form.error && !form.data) {
    return explain ? (
      <Banner tone="info">That match's questions aren't open to you any more. It may have been moved, called off or rolled.</Banner>
    ) : null
  }
  if (!form.data) return explain ? <Spinner label="Loading your questions" /> : null
  const data = form.data
  if (data.questions.length === 0) {
    return explain ? <Banner tone="info">That match has nothing for you to answer right now.</Banner> : null
  }
  const changed = data.questions.filter((q) => JSON.stringify(draft[q.key] ?? null) !== JSON.stringify(q.answer ?? null))
  const others = 'Your opponent'

  async function save() {
    setBusy(true)
    setNote(null)
    try {
      for (const q of changed) {
        await api
          .PUT('/api/matches/{match_id}/form/{key}', {
            params: { path: { match_id: matchId, key: q.key } },
            body: { value: draft[q.key] ?? null },
          })
          .then(unwrap)
      }
      setNote({ tone: 'info', text: 'Saved. You can change your answers until the seed rolls.' })
      form.reload()
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="mf-card" aria-labelledby={titleId}>
      <h2 className="mf-card__title" id={titleId} ref={heading} tabIndex={-1}>
        <Icon name="fact_check" size={22} />
        Before your match
      </h2>
      <p className="mf-card__lede">
        {data.locked
          ? 'The seed is rolled, so these answers are locked.'
          : `${data.questions.length === 1 ? 'One question' : `${data.questions.length} questions`}. You can change your answers until the seed rolls.`}
      </p>
      {note && <Banner tone={note.tone}>{note.text}</Banner>}
      {data.questions.map((q) => (
        <QuestionField
          key={q.key}
          q={q}
          value={draft[q.key] ?? null}
          locked={data.locked}
          others={others}
          onChange={(v) => setDraft({ ...draft, [q.key]: v })}
        />
      ))}
      {!data.locked && (
        <div className="mf-card__acts">
          <Button onClick={() => void save()} busy={busy} busyLabel="Saving…" disabled={changed.length === 0}>
            Save answers
          </Button>
        </div>
      )}
    </section>
  )
}

function QuestionField({
  q,
  value,
  locked,
  others,
  onChange,
}: {
  q: Question
  value: Value
  locked: boolean
  others: string
  onChange: (v: Value) => void
}) {
  const name = useId()
  const label = (v: unknown) => q.choices.find((c) => c.value === v)?.label ?? String(v)
  return (
    <fieldset className="mf-q" disabled={locked}>
      <legend className="mf-q__prompt">{q.prompt}</legend>
      <p className="mf-q__help">
        {q.help ? `${q.help} ` : ''}
        {seesLine(q, others)}
      </p>
      {q.kind === 'yes_no' ? (
        <div className="segmented" role="radiogroup" aria-label={q.prompt}>
          {['no', 'yes'].map((v) => (
            <span key={v}>
              <input type="radio" id={`${name}-${v}`} name={name} checked={value === v} onChange={() => onChange(v)} />
              <label htmlFor={`${name}-${v}`}>{label(v)}</label>
            </span>
          ))}
        </div>
      ) : q.kind === 'number' ? (
        <input
          className="input mf-q__number"
          type="number"
          aria-label={q.prompt}
          min={q.config.min}
          max={q.config.max}
          value={typeof value === 'number' ? value : ''}
          onChange={(e) => onChange(e.target.value === '' ? null : Math.trunc(Number(e.target.value)))}
        />
      ) : (
        <div className="mf-options">
          {q.choices.map((c) => {
            const v = c.value ?? ''
            const many = q.kind === 'choice_many'
            const picked = many ? Array.isArray(value) && value.includes(v) : value === v
            return (
              <label key={v} className="mf-option" data-picked={picked || undefined}>
                <input
                  type={many ? 'checkbox' : 'radio'}
                  name={name}
                  checked={picked}
                  onChange={(e) => {
                    if (!many) return onChange(v)
                    const list = Array.isArray(value) ? value : []
                    const next = e.target.checked ? [...list, v] : list.filter((x) => x !== v)
                    onChange(next.length ? next : null)
                  }}
                />
                <span>{c.label}</span>
              </label>
            )
          })}
        </div>
      )}
      {q.others.length > 0 && (
        <p className="mf-q__others">
          {q.others
            .map((o) => `${o.name} answered ${Array.isArray(o.value) ? o.value.map(label).join(', ') : label(o.value)}`)
            .join('. ')}
          .
        </p>
      )}
    </fieldset>
  )
}
