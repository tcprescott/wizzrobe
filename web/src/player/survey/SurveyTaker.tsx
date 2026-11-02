import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { Banner, Button, Chip, EmptyState, Icon, PageHeader } from '../../components'
import { SideListCard, SideListLayout, SideListRow } from '../../admin/kit/SideList'
import { effectiveZone, formatDay } from '../../lib/time'
import { usePhone } from '../../lib/usePhone'
import { QuestionField } from './Questions'
import { blank, minutes, missing, partShown, showing, type Answers, type Part, type Survey } from './visibility'
import './survey.css'

export interface TakerActions {
  /** Save one part; returns the survey as it stands after. */
  save: (part: Part, answers: Record<number, unknown>) => Promise<Survey>
  open?: (part: Part) => void
  submit: (anonymous: boolean) => Promise<Survey>
  decline?: () => Promise<void>
}

function initial(survey: Survey): Answers {
  return Object.fromEntries(survey.parts.map((p) => [p.key, { ...(p.answers as Record<number, unknown>) }]))
}

/**
 * A survey as the person taking it sees it: their parts in a side list beside
 * the open part, each part its own screen on a phone, answers saved as they
 * leave a part. Staff preview draws the same thing with nothing saved.
 */
export function SurveyTaker({
  survey: start,
  base,
  actions,
  preview,
  zone,
}: {
  survey: Survey
  base: string
  actions: TakerActions | null
  preview?: ReactNode
  zone?: string | null
}) {
  const { part: partParam } = useParams()
  const navigate = useNavigate()
  const phone = usePhone()
  const [survey, setSurvey] = useState(start)
  const [answers, setAnswers] = useState<Answers>(() => initial(start))
  const [dirty, setDirty] = useState<Set<string>>(new Set())
  const [anonymous, setAnonymous] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [flagged, setFlagged] = useState<Set<number>>(new Set())
  const opened = useRef(new Set<string>())

  const key = partParam ? decodeURIComponent(partParam) : null
  const shown = useMemo(() => survey.parts.filter((p) => partShown(p, survey.parts, answers)), [survey, answers])
  const part = shown.find((p) => p.key === key) ?? null
  const index = part ? shown.indexOf(part) : -1
  const next = index >= 0 ? shown[index + 1] : undefined
  const href = (p: Part) => `${base}/${encodeURIComponent(p.key)}`
  const editable = survey.open && !survey.submitted

  useEffect(() => {
    if (part && editable && actions?.open && !opened.current.has(part.key)) {
      opened.current.add(part.key)
      actions.open(part)
    }
  }, [part, editable, actions])

  if (!key && !phone && shown[0] && editable) return <Navigate to={href(shown[0])} replace />

  async function flush(p: Part | null) {
    if (!p || !actions || !dirty.has(p.key)) return true
    try {
      const after = await actions.save(p, answers[p.key] ?? {})
      setSurvey(after)
      setDirty((d) => {
        const next = new Set(d)
        next.delete(p.key)
        return next
      })
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      return false
    }
  }

  async function go(to: string) {
    setError(null)
    if (await flush(part)) navigate(to)
  }

  async function send() {
    setError(null)
    const left = missing(survey.parts, answers)
    if (left.length) {
      setFlagged(new Set(left.map((m) => m.question.id)))
      setError(`${left.length === 1 ? 'One required question is' : `${left.length} required questions are`} still blank.`)
      const first = left[0]
      if (first) navigate(href(first.part))
      return
    }
    if (!actions) return
    setBusy(true)
    try {
      if (!(await flush(part))) return
      setSurvey(await actions.submit(anonymous))
      navigate(base, { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  function change(p: Part, qid: number, value: unknown) {
    setAnswers((a) => ({ ...a, [p.key]: { ...(a[p.key] ?? {}), [qid]: value } }))
    setDirty((d) => new Set(d).add(p.key))
    if (!blank(value)) setFlagged((f) => (f.has(qid) ? new Set([...f].filter((x) => x !== qid)) : f))
  }

  const status = (p: Part) => {
    const qs = showing(p, survey.parts, answers)
    const done = qs.filter((q) => !blank(answers[p.key]?.[q.id])).length
    return { qs: qs.length, done }
  }

  const sendBlock = editable && (
    <div className="sv-send">
      {survey.allow_anonymous && (
        <label className="sv-anon">
          <input type="checkbox" checked={anonymous} onChange={(e) => setAnonymous(e.target.checked)} />
          <span>
            Send without my name
            <span className="sv-hint">Your name is removed for good; staff see only which groups you were in.</span>
          </span>
        </label>
      )}
      <Button variant="primary" busy={busy} disabled={!actions} onClick={() => void send()}>
        Send my answers
      </Button>
      {actions?.decline && (
        <button
          type="button"
          className="sv-decline"
          onClick={() => {
            void actions.decline?.().then(() => setError(null))
          }}
        >
          Not interested
        </button>
      )}
    </div>
  )

  const list = (
    <div className="side-list__col">
      <SideListCard label="Parts">
        {shown.map((p) => {
          const s = status(p)
          const done = s.qs > 0 && s.done === s.qs
          return (
            <SideListRow
              key={p.key}
              to={href(p)}
              leading={<Icon name={done ? 'check_circle' : 'radio_button_unchecked'} />}
              title={p.title}
              sub={`${s.qs} question${s.qs === 1 ? '' : 's'} · ${minutes(p.seconds).replace('about ', '')}`}
              end={<Chip tone={done ? 'ok' : 'neutral'}>{done ? 'Done' : s.done ? `${s.done} of ${s.qs}` : 'To do'}</Chip>}
            />
          )
        })}
      </SideListCard>
      {sendBlock}
    </div>
  )

  const detail = part ? (
    <section className="sv-part" aria-labelledby="sv-part-title">
      <header className="sv-part__head">
        <div>
          <h2 id="sv-part-title">{part.title}</h2>
          {part.description && <p className="sv-part__desc">{part.description}</p>}
          {part.reason && <p className="sv-hint">{part.reason}</p>}
        </div>
        <span className="sv-part__count">
          Part {index + 1} of {shown.length}
        </span>
      </header>
      {showing(part, survey.parts, answers).map((q) => (
        <QuestionField
          key={q.id}
          q={q}
          answer={answers[part.key]?.[q.id]}
          error={flagged.has(q.id)}
          onChange={(v) => editable && change(part, q.id, v)}
        />
      ))}
      <footer className="sv-part__foot">
        <Button variant="ghost" onClick={() => void go(base)}>
          Back to parts
        </Button>
        {next ? (
          <Button variant="primary" onClick={() => void go(href(next))}>
            Next part
          </Button>
        ) : (
          editable && (
            <Button variant="primary" onClick={() => void go(base)}>
              Done
            </Button>
          )
        )}
      </footer>
    </section>
  ) : key ? (
    <EmptyState icon="rate_review" title="That part isn't here">
      An earlier answer may have hidden it.
    </EmptyState>
  ) : null

  const closes = survey.closes_at ? formatDay(survey.closes_at, effectiveZone(zone)) : null
  return (
    <div className="sv">
      {(!phone || !part) && (
        <PageHeader
          title={survey.title}
          lede={
            <>
              {minutes(survey.seconds).replace(/^a/, 'A')}. Only the parts that fit what you did.{' '}
              {closes && editable && <Chip tone="info">Closes {closes}</Chip>}
            </>
          }
        />
      )}
      {phone && part && <h1 className="visually-hidden">{survey.title}</h1>}
      {preview}
      {survey.intro && (!phone || !part) && <p className="sv-intro">{survey.intro}</p>}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {survey.submitted ? (
        <Banner tone="accent">
          <strong>Thanks, your answers are in.</strong> {survey.after_send}
          {survey.anonymous ? ' They went without your name.' : ''}
        </Banner>
      ) : !survey.open && !preview ? (
        <Banner tone="info">
          <strong>This survey has closed.</strong> It isn't taking answers any more.
        </Banner>
      ) : (
        <SideListLayout list={list} detail={detail} open={Boolean(key)} backTo={base} backLabel="Parts" />
      )}
      {survey.submitted && (
        <p>
          <Link to=".." relative="path">
            Back
          </Link>
        </p>
      )}
    </div>
  )
}
