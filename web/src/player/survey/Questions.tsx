import { useId } from 'react'
import { bare, type Question } from './visibility'

const AGREE = ['Strongly disagree', 'Disagree', 'Neither', 'Agree', 'Strongly agree']
const OTHER = 'Other'

type Change = (value: unknown) => void

function Scale({ q, value, onChange, low, high }: { q: Question; value: unknown; onChange: Change; low: number; high: number }) {
  const name = useId()
  const steps = Array.from({ length: high - low + 1 }, (_, i) => low + i)
  return (
    <div className="sv-scale">
      <div className="sv-scale__row" role="radiogroup" aria-label={q.prompt}>
        {steps.map((n) => (
          <label key={n} className="sv-scale__step">
            <input type="radio" name={name} checked={value === n} onChange={() => onChange(n)} />
            <span>{n}</span>
          </label>
        ))}
      </div>
      <div className="sv-scale__ends" aria-hidden="true">
        <span>{q.kind === 'nps' ? 'Not likely' : 'Poor'}</span>
        <span>{q.kind === 'nps' ? 'Very likely' : 'Great'}</span>
      </div>
      {value !== undefined && value !== null && (
        <button type="button" className="sv-clear" onClick={() => onChange(null)}>
          Clear
        </button>
      )}
    </div>
  )
}

function YesNo({ q, value, onChange }: { q: Question; value: unknown; onChange: Change }) {
  const name = useId()
  return (
    <div className="sv-seg" role="radiogroup" aria-label={q.prompt}>
      {['Yes', 'No'].map((v) => (
        <label key={v}>
          <input type="radio" name={name} checked={value === v} onChange={() => onChange(v)} />
          <span>{v}</span>
        </label>
      ))}
    </div>
  )
}

function Choices({ q, answer, onChange }: { q: Question; answer: unknown; onChange: Change }) {
  const name = useId()
  const many = q.kind === 'choice_many'
  const value = bare(answer)
  const other = answer && typeof answer === 'object' && !Array.isArray(answer) ? String((answer as { other?: string }).other ?? '') : ''
  const picked: string[] = many ? (Array.isArray(value) ? (value as string[]) : []) : value ? [String(value)] : []
  const options = [...(q.options ?? []), ...(q.other_option ? [OTHER] : []), ...(many && q.none_option ? [q.none_option] : [])]

  function emit(next: string[], write: string) {
    const v = many ? next : (next[0] ?? null)
    onChange(next.includes(OTHER) ? { value: v, other: write } : v)
  }
  function toggle(option: string, on: boolean) {
    if (!many) return emit([option], other)
    if (option === q.none_option) return emit(on ? [option] : [], other)
    const rest = picked.filter((p) => p !== q.none_option && p !== option)
    emit(on ? [...rest, option] : rest, other)
  }
  const limits =
    many && (q.min_picks || q.max_picks)
      ? q.min_picks && q.max_picks
        ? q.min_picks === q.max_picks
          ? `Pick ${q.min_picks}`
          : `Pick ${q.min_picks} to ${q.max_picks}`
        : q.max_picks
          ? `Pick up to ${q.max_picks}`
          : `Pick at least ${q.min_picks}`
      : null
  return (
    <fieldset className="sv-choices">
      <legend className="visually-hidden">{q.prompt}</legend>
      {limits && <span className="sv-hint">{limits}</span>}
      {options.map((o) => (
        <label key={o} className="sv-choice">
          <input
            type={many ? 'checkbox' : 'radio'}
            name={name}
            checked={picked.includes(o)}
            onChange={(e) => toggle(o, e.target.checked)}
          />
          <span>{o === OTHER ? 'Other' : o}</span>
        </label>
      ))}
      {picked.includes(OTHER) && (
        <input
          className="input sv-other"
          aria-label={`Other answer for ${q.prompt}`}
          maxLength={200}
          value={other}
          onChange={(e) => emit(picked, e.target.value)}
        />
      )}
    </fieldset>
  )
}

function Agree({ q, value, onChange }: { q: Question; value: unknown; onChange: Change }) {
  const base = useId()
  const levels = (value && typeof value === 'object' ? value : {}) as Record<string, number>
  return (
    <div className="sv-agree">
      {(q.options ?? []).map((statement, i) => (
        <fieldset key={statement} className="sv-agree__row">
          <legend>{statement}</legend>
          <div className="sv-agree__levels">
            {AGREE.map((label, n) => (
              <label key={label}>
                <input
                  type="radio"
                  name={`${base}-${i}`}
                  checked={levels[statement] === n + 1}
                  onChange={() => onChange({ ...levels, [statement]: n + 1 })}
                />
                <span>{label}</span>
              </label>
            ))}
          </div>
        </fieldset>
      ))}
    </div>
  )
}

/** One question with its control. Answers come back in the shape `save_part` takes. */
export function QuestionField({
  q,
  answer,
  onChange,
  error,
}: {
  q: Question
  answer: unknown
  onChange: Change
  error?: boolean
}) {
  const id = useId()
  const value = bare(answer)
  let control
  if (q.kind === 'rating') control = <Scale q={q} value={value} onChange={onChange} low={1} high={5} />
  else if (q.kind === 'nps') control = <Scale q={q} value={value} onChange={onChange} low={0} high={10} />
  else if (q.kind === 'yes_no') control = <YesNo q={q} value={value} onChange={onChange} />
  else if (q.kind === 'choice_one' || q.kind === 'choice_many') control = <Choices q={q} answer={answer} onChange={onChange} />
  else if (q.kind === 'agree_scale') control = <Agree q={q} value={value} onChange={onChange} />
  else
    control = (
      <textarea
        id={id}
        className="input sv-comment"
        rows={q.long_answer ? 6 : 3}
        maxLength={q.long_answer ? 3000 : 1000}
        value={typeof answer === 'string' ? answer : ''}
        onChange={(e) => onChange(e.target.value)}
      />
    )
  return (
    <div className={`sv-q${error ? ' sv-q--missing' : ''}${q.kind === 'yes_no' ? ' sv-q--inline' : ''}`}>
      {q.kind === 'comment' ? (
        <label className="sv-q__prompt" htmlFor={id}>
          {q.prompt} {!q.required && <span className="sv-q__optional">Optional</span>}
        </label>
      ) : (
        <p className="sv-q__prompt">
          {q.prompt} {!q.required && <span className="sv-q__optional">Optional</span>}
        </p>
      )}
      {control}
      {error && <span className="sv-q__error">This one needs an answer.</span>}
    </div>
  )
}
