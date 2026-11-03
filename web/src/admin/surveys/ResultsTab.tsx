import { Link, useSearchParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../../api/client'
import { EmptyState, Spinner } from '../../components'
import { LoadError, useAdminQuery } from '../kit'
import { kindOf, mins, plural } from './data'
import type { SurveyCtx } from './SurveyPage'

type Result = Schemas['QuestionResultResponse']
type Breakdown = Schemas['BreakdownResponse']

const pct = (n: number, of: number) => (of ? Math.round((n / of) * 100) : 0)

function Bars({ counts, total }: { counts: Record<string, number>; total: number }) {
  return (
    <ul className="sv-bars">
      {Object.entries(counts).map(([label, n]) => (
        <li key={label}>
          <span className="sv-bars__label">{label}</span>
          <span className="sv-bars__track" aria-hidden="true">
            <span style={{ width: `${pct(n, total)}%` }} />
          </span>
          <span className="sv-bars__n">
            {pct(n, total)}% <span className="sv-hint">({n})</span>
          </span>
        </li>
      ))}
    </ul>
  )
}

function Summary({ q, b }: { q: Result; b: Breakdown }) {
  if (b.hidden) return <p className="sv-hint">Fewer than 5 answered ({b.count}), so only the count shows.</p>
  if (q.kind === 'rating')
    return (
      <p className="sv-result__big">
        {b.average?.toFixed(1) ?? '—'} <span className="sv-hint">average · {b.top_share ?? 0}% rated 4 or 5 · n={b.count}</span>
      </p>
    )
  if (q.kind === 'nps')
    return (
      <p className="sv-result__big">
        {b.score ?? '—'}{' '}
        <span className="sv-hint">
          recommend score · average {b.average?.toFixed(1) ?? '—'} · n={b.count}
          {b.count < 50 ? ' · small sample' : ''}
        </span>
      </p>
    )
  if (q.kind === 'comment')
    return <p className="sv-hint">{plural(b.count, 'comment')}. Read them under Comments.</p>
  if (q.kind === 'agree_scale')
    return (
      <ul className="sv-bars">
        {Object.entries(b.statements ?? {}).map(([statement, levels]) => {
          const total = levels.reduce((a, n) => a + n, 0)
          const agree = (levels[3] ?? 0) + (levels[4] ?? 0)
          return (
            <li key={statement}>
              <span className="sv-bars__label">{statement}</span>
              <span className="sv-bars__track" aria-hidden="true">
                <span style={{ width: `${pct(agree, total)}%` }} />
              </span>
              <span className="sv-bars__n">{pct(agree, total)}% agree</span>
            </li>
          )
        })}
      </ul>
    )
  return <Bars counts={b.counts ?? {}} total={b.count} />
}

/** Results: the numbers per question, narrowed to one group when asked. */
export function ResultsTab({ survey, base }: SurveyCtx) {
  const [params, setParams] = useSearchParams()
  const group = params.get('group') || ''
  const results = useAdminQuery(`surveys/${survey.id}/results?${group}`, () =>
    api
      .GET('/api/surveys/{survey_id}/results', {
        params: { path: { survey_id: survey.id }, query: group ? { group } : {} },
      })
      .then(unwrap),
  )
  if (survey.status === 'draft')
    return (
      <EmptyState icon="rate_review" title="Nothing yet">
        Results show up here once it's sent and people answer.
      </EmptyState>
    )
  if (results.error && !results.data) return <LoadError error={results.error} onRetry={results.reload} />
  if (!results.data) return <Spinner label="Counting answers" />
  const r = results.data
  const sections: { title: string; questions: Result[] }[] = []
  for (const q of r.questions) {
    const last = sections[sections.length - 1]
    if (last && last.title === q.section_title) last.questions.push(q)
    else sections.push({ title: q.section_title, questions: [q] })
  }

  return (
    <div className="sv-results">
      <dl className="sv-stats">
        <div>
          <dt>Sent to</dt>
          <dd>{r.recipients}</dd>
        </div>
        <div>
          <dt>Opened</dt>
          <dd>{r.opened}</dd>
        </div>
        <div>
          <dt>Answered</dt>
          <dd>
            {r.responses} <span className="sv-hint">{pct(r.responses, r.recipients)}%</span>
          </dd>
        </div>
        <div>
          <dt>Without a name</dt>
          <dd>{r.anonymous}</dd>
        </div>
      </dl>
      {(r.groups?.length ?? 0) > 0 && (
        <label className="field sv-group">
          <span className="field__label">Show</span>
          <select
            className="input"
            value={group}
            onChange={(e) => setParams(e.target.value ? { group: e.target.value } : {}, { replace: true })}
          >
            <option value="">Everyone</option>
            {r.groups?.map((g) => (
              <option key={g.key} value={g.key}>
                {g.label} ({g.responded} of {g.sent})
              </option>
            ))}
          </select>
        </label>
      )}
      {!group && (r.audiences?.length ?? 0) > 0 && (
        <p className="sv-hint">
          Answered:{' '}
          {r.audiences?.map((a) => `${a.label} ${pct(a.responded, a.sent)}% (${a.responded} of ${a.sent})`).join(' · ')}
        </p>
      )}
      {sections.map((s) => (
        <section key={s.title} className="sv-card" aria-label={s.title}>
          <h2 className="sv-card__title">{s.title}</h2>
          {s.questions.map((q) => (
            <article key={q.question_id} className="sv-result">
              <h3 className="sv-result__q">{q.prompt}</h3>
              <p className="sv-hint">
                {kindOf(q.kind).label}
                {q.condition ? ` · ${q.condition}` : ''}
              </p>
              <Summary q={q} b={q.overall} />
              {Object.keys(q.groups ?? {}).length > 0 && q.kind !== 'comment' && (
                <details>
                  <summary>By {plural(Object.keys(q.groups ?? {}).length, 'group')}</summary>
                  {Object.entries(q.groups ?? {}).map(([name, b]) => (
                    <div key={name} className="sv-result__group">
                      <p className="sv-result__gname">{name}</p>
                      <Summary q={q} b={b} />
                    </div>
                  ))}
                </details>
              )}
            </article>
          ))}
        </section>
      ))}
      {r.dropoff && (
        <section className="sv-card" aria-labelledby="sv-dropoff">
          <h2 id="sv-dropoff" className="sv-card__title">
            Where people stopped
          </h2>
          <p className="sv-hint">
            {r.dropoff.started} started · {r.dropoff.declined} said not interested
            {r.dropoff.median_seconds ? ` · took ${mins(r.dropoff.median_seconds)} (median)` : ''}
          </p>
          <ul className="sv-bars">
            {r.dropoff.parts.map((p) => (
              <li key={p.title}>
                <span className="sv-bars__label">{p.title}</span>
                <span className="sv-bars__track" aria-hidden="true">
                  <span style={{ width: `${pct(p.answered, r.dropoff?.started ?? 0)}%` }} />
                </span>
                <span className="sv-bars__n">
                  {p.answered} answered{p.stopped ? ` · ${p.stopped} stopped here` : ''}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
      <p>
        <Link to={`${base}/comments`}>Read the comments</Link>
      </p>
    </div>
  )
}
