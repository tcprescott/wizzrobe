import { useState } from 'react'
import { api, unwrap } from '../../api/client'
import { Banner, Button, Spinner } from '../../components'
import { formatDay, formatDayTime, wallClock, zonedInstant } from '../../lib/time'
import { LoadError, useAdminQuery } from '../kit'
import { mins, plural } from './data'
import { ConfirmDialog } from './dialogs'
import type { SurveyCtx } from './SurveyPage'

function WhoSeesWhat({ surveyId }: { surveyId: number }) {
  const paths = useAdminQuery(`surveys/${surveyId}/preview-paths`, () =>
    api.GET('/api/surveys/{survey_id}/preview-paths', { params: { path: { survey_id: surveyId } } }).then(unwrap),
  )
  if (paths.error && !paths.data) return <LoadError error={paths.error} onRetry={paths.reload} />
  if (!paths.data) return <Spinner label="Working out who sees what" />
  return (
    <section className="sv-card" aria-labelledby="sv-who">
      <h2 id="sv-who" className="sv-card__title">
        Who sees what
      </h2>
      <ul className="sv-paths">
        {paths.data.map((p) => {
          const hidden = p.hidden ?? []
          return (
          <li key={p.persona}>
            <div className="sv-paths__row">
              <span className="sv-paths__who">{p.label}</span>
              <span className="sv-hint">
                {plural(p.parts, 'part')} · {plural(p.questions, 'question')} · {mins(p.seconds)}
              </span>
            </div>
            {hidden.length > 0 && (
              <details>
                <summary>{plural(hidden.length, 'thing')} they won't see</summary>
                <ul>
                  {hidden.map((h) => (
                    <li key={h}>{h}</li>
                  ))}
                </ul>
              </details>
            )}
          </li>
          )
        })}
      </ul>
    </section>
  )
}

/** Send: who it goes to and when it closes; once sent, how it's going and the reminder. */
export function SendTab({ survey, row, reload }: SurveyCtx) {
  const builder = useAdminQuery(`surveys/${survey.id}/builder`, () =>
    api.GET('/api/surveys/{survey_id}/builder', { params: { path: { survey_id: survey.id } } }).then(unwrap),
  )
  const zone = builder.data?.zone ?? 'UTC'
  const week = wallClock(new Date(Date.now() + 7 * 86400_000).toISOString(), zone)
  const [date, setDate] = useState('')
  const [time, setTime] = useState('23:59')
  const [confirm, setConfirm] = useState<'send' | 'remind' | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const path = { survey_id: survey.id }
  const closeDate = date || week.date

  if (survey.status === 'draft') {
    const closesAt = zonedInstant(closeDate, time || '23:59', zone)
    return (
      <div className="sv-send-tab">
        <section className="sv-card" aria-labelledby="sv-send">
          <h2 id="sv-send" className="sv-card__title">
            Send it
          </h2>
          <p>
            It goes by Discord DM to everyone who took part in this event: players, volunteers, crew and anyone who
            checked in. Each person only sees the parts that fit what they did.
          </p>
          <div className="field-grid">
            <label className="field">
              <span className="field__label">Closes on</span>
              <input className="input" type="date" value={closeDate} onChange={(e) => setDate(e.target.value)} />
            </label>
            <label className="field">
              <span className="field__label">At</span>
              <input className="input" type="time" value={time} onChange={(e) => setTime(e.target.value)} />
            </label>
          </div>
          <Button variant="primary" icon="send" onClick={() => setConfirm('send')}>
            Send…
          </Button>
        </section>
        <WhoSeesWhat surveyId={survey.id} />
        {confirm === 'send' && (
          <ConfirmDialog
            title="Send to everyone who took part?"
            label="Send survey"
            busyLabel="Sending…"
            onClose={() => setConfirm(null)}
            action={async () => {
              const r = await api
                .POST('/api/surveys/{survey_id}/send', { params: { path }, body: { closes_at: closesAt.toISOString() } })
                .then(unwrap)
              setConfirm(null)
              setNote(`Sent to ${plural(r.people, 'person', 'people')}.`)
              reload()
            }}
          >
            Everyone gets a DM now, and the survey closes {formatDayTime(closesAt.toISOString(), zone)}. Its parts and
            questions are fixed once it's sent.
          </ConfirmDialog>
        )}
      </div>
    )
  }

  const nextAt = row?.next_reminder_at ? new Date(row.next_reminder_at) : null
  const canRemind = survey.status === 'open' && (row?.reminders_left ?? 0) > 0 && (!nextAt || nextAt <= new Date())
  const lastCall = row?.reminders_left === 1
  return (
    <div className="sv-send-tab">
      {note && (
        <p className="settings-saved" role="status">
          {note}
        </p>
      )}
      <section className="sv-card" aria-labelledby="sv-progress">
        <h2 id="sv-progress" className="sv-card__title">
          {survey.status === 'open' ? 'How it’s going' : 'How it went'}
        </h2>
        <dl className="sv-stats">
          <div>
            <dt>Sent to</dt>
            <dd>{row?.recipients ?? '—'}</dd>
          </div>
          <div>
            <dt>Answered</dt>
            <dd>{row?.answered ?? '—'}</dd>
          </div>
          <div>
            <dt>{survey.status === 'open' ? 'Closes' : 'Closed'}</dt>
            <dd>{survey.closes_at ? formatDay(survey.closes_at, zone) : 'When you close it'}</dd>
          </div>
        </dl>
      </section>
      {survey.status === 'open' && (
        <section className="sv-card" aria-labelledby="sv-remind">
          <h2 id="sv-remind" className="sv-card__title">
            Remind people
          </h2>
          <p>
            A DM to everyone who hasn't answered or said "Not interested". Once a day, two in all, and the second is a
            last call.
          </p>
          {(row?.reminders_left ?? 0) === 0 ? (
            <Banner tone="info">Both reminders have gone out.</Banner>
          ) : (
            <>
              <Button variant="outline" icon="notifications" disabled={!canRemind} onClick={() => setConfirm('remind')}>
                {lastCall ? 'Send the last call…' : 'Remind…'}
              </Button>
              {!canRemind && nextAt && <p className="sv-hint">The next one can go {formatDayTime(nextAt.toISOString(), zone)}.</p>}
            </>
          )}
        </section>
      )}
      {confirm === 'remind' && (
        <ConfirmDialog
          title={lastCall ? 'Send the last call?' : 'Send a reminder?'}
          label={lastCall ? 'Send last call' : 'Send reminder'}
          busyLabel="Sending…"
          onClose={() => setConfirm(null)}
          action={async () => {
            const r = await api.POST('/api/surveys/{survey_id}/remind', { params: { path } }).then(unwrap)
            setConfirm(null)
            setNote(`Reminded ${plural(r.people, 'person', 'people')}.`)
            reload()
          }}
        >
          Everyone still waiting gets a DM now.{lastCall ? " It's the last one you can send." : ''}
        </ConfirmDialog>
      )}
    </div>
  )
}
