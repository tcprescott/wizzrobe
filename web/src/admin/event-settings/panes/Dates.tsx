import { useId } from 'react'
import { api, unwrap, type Schemas } from '../../../api/client'
import { Spinner } from '../../../components'
import { LoadError, SettingsCard, useDraft } from '../../kit'
import { dayLabel, useEventParams, useEventSetting } from '../data'

type Dates = Schemas['EventDatesResponse']

function DatesCard({ data, onSaved }: { data: Dates; onSaved: () => void }) {
  const params = useEventParams()
  const draft = useDraft({ starts_on: data.starts_on ?? '', ends_on: data.ends_on ?? '' })
  const value = draft.value!
  return (
    <SettingsCard
      title="Event dates"
      icon="date_range"
      subtitle="Leave both blank and the event runs from its first scheduled match to its last."
      dirty={draft.dirty}
      onDiscard={draft.discard}
      onSave={async () => {
        await api
          .PUT('/api/event-settings/dates', {
            ...params,
            body: { starts_on: value.starts_on || null, ends_on: value.ends_on || null },
          })
          .then(unwrap)
        draft.discard()
        onSaved()
      }}
    >
      <div className="field-grid">
        <label className="field">
          <span className="field__label">Starts</span>
          <input
            className="input"
            type="date"
            value={value.starts_on}
            onChange={(e) => draft.patch({ starts_on: e.target.value })}
          />
        </label>
        <label className="field">
          <span className="field__label">Ends</span>
          <input
            className="input"
            type="date"
            value={value.ends_on}
            min={value.starts_on || undefined}
            onChange={(e) => draft.patch({ ends_on: e.target.value })}
          />
        </label>
      </div>
    </SettingsCard>
  )
}

function HoursCard({ data, onSaved }: { data: Dates; onSaved: () => void }) {
  const id = useId()
  const params = useEventParams()
  const draft = useDraft(data.days)
  const days = draft.value ?? []
  const setDay = (i: number, part: Partial<Schemas['DayHours']>) =>
    draft.set(days.map((d, j) => (j === i ? { ...d, ...part } : d)))
  return (
    <SettingsCard
      title="Daily hours"
      icon="schedule"
      subtitle={`Matches can only be booked to start inside these hours, on the event's clock (${data.timezone_label}). Leave a day blank to allow any time.`}
      dirty={draft.dirty}
      onDiscard={draft.discard}
      onSave={async () => {
        const body = days.map((d) => ({ day: d.day, opens: d.opens || null, closes: d.closes || null }))
        await api.PUT('/api/event-settings/hours', { ...params, body: { days: body } }).then(unwrap)
        draft.discard()
        onSaved()
      }}
    >
      {days.length === 0 ? (
        <p className="settings-note">Set the event's dates, or book a match, and its days appear here.</p>
      ) : (
        <div className="settings-hours" role="group" aria-labelledby={`${id}-head`}>
          <span className="settings-hours__head" id={`${id}-head`}>
            Day
          </span>
          <span className="settings-hours__head" aria-hidden="true">
            Opens
          </span>
          <span className="settings-hours__head" aria-hidden="true">
            Closes
          </span>
          {days.map((d, i) => {
            const label = dayLabel(d.day)
            return (
              <div key={d.day} className="settings-hours__row">
                <span className="settings-hours__day">{label}</span>
                <input
                  className="input"
                  type="time"
                  aria-label={`${label}, opens`}
                  value={d.opens ?? ''}
                  onChange={(e) => setDay(i, { opens: e.target.value || null })}
                />
                <input
                  className="input"
                  type="time"
                  aria-label={`${label}, closes`}
                  value={d.closes ?? ''}
                  onChange={(e) => setDay(i, { closes: e.target.value || null })}
                />
              </div>
            )
          })}
        </div>
      )}
    </SettingsCard>
  )
}

export function DatesAndHours() {
  const params = useEventParams()
  const { data, error, reload } = useEventSetting('dates', () => api.GET('/api/event-settings/dates', params).then(unwrap))
  if (error && !data) return <LoadError error={error} onRetry={reload} />
  if (!data) return <Spinner label="Loading the event's dates" />
  const key = `${data.starts_on}-${data.ends_on}-${data.days.length}`
  return (
    <>
      <DatesCard key={`d-${key}`} data={data} onSaved={reload} />
      <HoursCard key={`h-${key}`} data={data} onSaved={reload} />
    </>
  )
}
