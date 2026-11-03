import { useId } from 'react'
import { api, unwrap } from '../../../api/client'
import { Spinner } from '../../../components'
import { formatDayTime } from '../../../lib/time'
import { LoadError, MinutesField, RadioRows, SettingsCard, SwitchRow, useDraft } from '../../kit'
import { useEventParams, useEventSetting } from '../data'

export function Capacity() {
  const params = useEventParams()
  const { data, error, reload } = useEventSetting('capacity', () => api.GET('/api/event-settings/capacity', params).then(unwrap))
  const draft = useDraft(data)
  if (error && !data) return <LoadError error={error} onRetry={reload} />
  if (!draft.value) return <Spinner label="Loading capacity" />
  const value = draft.value
  return (
    <SettingsCard
      title="Capacity"
      icon="groups_2"
      subtitle="How much the scheduler lets run at the same time."
      dirty={draft.dirty}
      onDiscard={draft.discard}
      onSave={async () => {
        await api.PUT('/api/event-settings/capacity', { ...params, body: value }).then(unwrap)
        draft.discard()
        reload()
      }}
    >
      <MinutesField
        label="Players at once"
        hint="Blank uses 60."
        value={value.max_players ?? null}
        onChange={(n) => draft.patch({ max_players: n })}
      />
      <MinutesField
        label="Stages at once"
        hint="Blank uses the number of active stages."
        value={value.max_stages ?? null}
        onChange={(n) => draft.patch({ max_stages: n })}
      />
    </SettingsCard>
  )
}

export function Crew() {
  const params = useEventParams()
  const { data, error, reload } = useEventSetting('crew', () => api.GET('/api/event-settings/crew', params).then(unwrap))
  const draft = useDraft(data)
  if (error && !data) return <LoadError error={error} onRetry={reload} />
  if (!draft.value) return <Spinner label="Loading crew settings" />
  const value = draft.value
  return (
    <SettingsCard
      title="Broadcasters"
      icon="podcasts"
      subtitle="A third crew role next to commentators and trackers."
      dirty={draft.dirty}
      onDiscard={draft.discard}
      onSave={async () => {
        await api.PUT('/api/event-settings/crew', { ...params, body: value }).then(unwrap)
        draft.discard()
        reload()
      }}
    >
      <SwitchRow
        label="Broadcasters on matches"
        hint="When on, anyone in the community can sign up to broadcast a match, and staff approve them like commentators. Each tournament sets how many it needs (1 unless you change it, 0 for none). Only this event changes."
        checked={value.broadcasters}
        onChange={(on) => draft.set({ broadcasters: on })}
      />
    </SettingsCard>
  )
}

export function RewardTiers() {
  const id = useId()
  const params = useEventParams()
  const { data, error, reload } = useEventSetting('reward-tiers', () =>
    api.GET('/api/event-settings/reward-tiers', params).then(unwrap),
  )
  const draft = useDraft(data && { text: data.text })
  if (error && !data) return <LoadError error={error} onRetry={reload} />
  if (!data || !draft.value) return <Spinner label="Loading reward tiers" />
  const value = draft.value
  const inEffect = data.tiers.length === 1 && data.tiers[0] === 0 ? 'none' : data.tiers.map((t) => `${t} h`).join(', ')
  return (
    <SettingsCard
      title="Reward tiers"
      icon="military_tech"
      subtitle="The hour totals that earn a volunteer something at this event."
      dirty={draft.dirty}
      onDiscard={draft.discard}
      onSave={async () => {
        await api.PUT('/api/event-settings/reward-tiers', { ...params, body: value }).then(unwrap)
        draft.discard()
        reload()
      }}
    >
      <div className="field settings-text-field">
        <label className="field__label" htmlFor={id}>
          Reward tiers (hours)
        </label>
        <input
          id={id}
          className="input"
          inputMode="decimal"
          placeholder="8, 12, 16"
          value={value.text}
          aria-describedby={`${id}-hint`}
          onChange={(e) => draft.set({ text: e.target.value })}
        />
        <span className="field__hint" id={`${id}-hint`}>
          Hour totals separated by commas. Blank uses 8, 12, 16. Enter 0 if this event awards nothing by hours. In
          effect now: {inEffect}.
        </span>
      </div>
    </SettingsCard>
  )
}

export function EventClock() {
  const zoneId = useId()
  const params = useEventParams()
  const { data, error, reload } = useEventSetting('timezone', () => api.GET('/api/event-settings/timezone', params).then(unwrap))
  const draft = useDraft(data && { follows_community: data.follows_community, mode: data.mode, name: data.name })
  if (error && !data) return <LoadError error={error} onRetry={reload} />
  if (!data || !draft.value) return <Spinner label="Loading the event's clock" />
  const value = draft.value
  const community =
    data.community_mode === 'pinned'
      ? `one timezone for everybody, ${data.community_name.replace(/_/g, ' ')}`
      : `each person's own timezone, falling back to ${data.community_name.replace(/_/g, ' ')}`
  let preview = ''
  try {
    preview = formatDayTime(new Date().toISOString(), value.name)
  } catch {
    preview = ''
  }
  return (
    <SettingsCard
      title="Clock"
      icon="schedule"
      subtitle="The clock this event's times are shown on. Times are always stored precisely."
      dirty={draft.dirty}
      onDiscard={draft.discard}
      onSave={async () => {
        await api.PUT('/api/event-settings/timezone', { ...params, body: value }).then(unwrap)
        draft.discard()
        reload()
      }}
    >
      <RadioRows
        label="Which clock"
        name="event-tz-follow"
        value={value.follows_community ? 'community' : 'own'}
        options={[
          { value: 'community', label: 'Same as the community', hint: `The community shows ${community}.` },
          {
            value: 'own',
            label: 'Its own timezone',
            hint: "For one on-site weekend in a community that's otherwise online. Other events aren't affected.",
          },
        ]}
        onChange={(v) => draft.patch({ follows_community: v === 'community' })}
      />
      {!value.follows_community && (
        <>
          <hr className="settings-divider" />
          <RadioRows
            label="Whose clock"
            name="event-tz-mode"
            value={value.mode}
            options={[
              { value: 'pinned', label: 'Show one timezone to everybody' },
              { value: 'user', label: "Follow each person's own timezone" },
            ]}
            onChange={(mode) => draft.patch({ mode })}
          />
          <div className="field settings-number">
            <label className="field__label" htmlFor={zoneId}>
              Timezone
            </label>
            <select id={zoneId} className="input" value={value.name} onChange={(e) => draft.patch({ name: e.target.value })}>
              {data.options.map((z) => (
                <option key={z} value={z}>
                  {z.replace(/_/g, ' ')}
                </option>
              ))}
            </select>
            {preview && <span className="field__hint">Right now it reads {preview}.</span>}
          </div>
        </>
      )}
    </SettingsCard>
  )
}
