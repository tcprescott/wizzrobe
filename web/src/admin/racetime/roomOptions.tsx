import { useId } from 'react'
import { api, unwrap, type Schemas } from '../../api/client'
import { Icon } from '../../components'
import { useAdminQuery } from '../kit'
import './racetime-settings.css'

type Pick = Schemas['PickOption']

/** The bots, room profiles, Discord channels and roles a racetime settings form offers. */
export function useRoomOptions() {
  return useAdminQuery('group-races/options', () => api.GET('/api/group-races/options').then(unwrap))
}

/**
 * A select over picked ids, with a "none" choice. A saved id that isn't in the
 * list (a deleted channel, Discord unreachable) stays selectable by its id, so
 * opening the form never silently changes it.
 */
export function PickSelect({
  label,
  hint,
  value,
  options,
  none,
  prefix = '',
  onChange,
}: {
  label: string
  hint?: string
  value: string | null
  options: Pick[] | undefined
  none: string
  prefix?: string
  onChange: (value: string | null) => void
}) {
  const id = useId()
  const list = options ?? []
  const missing = value !== null && !list.some((o) => o.id === value)
  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>
        {label}
      </label>
      <select id={id} className="input" value={value ?? ''} onChange={(e) => onChange(e.target.value || null)}>
        <option value="">{none}</option>
        {missing && <option value={value!}>{`Unknown (${value})`}</option>}
        {list.map((o) => (
          <option key={o.id} value={o.id}>
            {prefix}
            {o.name}
          </option>
        ))}
      </select>
      {hint && <span className="field__hint">{hint}</span>}
    </div>
  )
}

/** What the bot's Discord post will look like, for the settings preview. */
export function DiscordPostPreview({
  title,
  channel,
  role,
  seedMinutes,
  locks,
}: {
  title: string
  channel: string | null
  role: string | null
  seedMinutes: number
  locks: boolean
}) {
  if (!channel) return <p className="rt-note">Pick a channel to post each room in Discord as it opens.</p>
  return (
    <div className="rt-post" aria-label={`Preview of the post in #${channel}`}>
      <div className="rt-post__head">
        <strong>Wizzrobe</strong> <span className="rt-post__muted">in #{channel}</span>
      </div>
      <div>
        {role && <span className="rt-post__mention">@{role}</span>} The race room for {title} is open. Anyone can join.
      </div>
      <div className="rt-post__embed">
        <strong>🏁 {title}</strong>
        <span className="rt-post__muted">
          Starts at the race time · seed {seedMinutes > 0 ? `${seedMinutes} min before` : 'at the start'}
          {locks ? ', and the room locks then' : ''}
        </span>
      </div>
      <span className="rt-post__button">
        <Icon name="open_in_new" /> Join the race room
      </span>
    </div>
  )
}
