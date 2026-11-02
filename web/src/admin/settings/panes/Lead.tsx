import { api, unwrap } from '../../../api/client'
import { useQuery } from '../../../api/useQuery'
import { Spinner } from '../../../components'
import { LoadError, MinutesField, SettingsCard, SwitchRow, useDraft } from '../../kit'

export function Volunteers() {
  const { data, error, reload } = useQuery('settings-volunteers', () => api.GET('/api/settings/volunteers').then(unwrap))
  const draft = useDraft(data)
  if (error && !data) return <LoadError error={error} onRetry={reload} />
  if (!draft.value) return <Spinner label="Loading volunteer settings" />
  const value = draft.value
  return (
    <SettingsCard
      title="Shifts"
      icon="volunteer_activism"
      subtitle="When volunteers hear about their next shift."
      dirty={draft.dirty}
      onDiscard={draft.discard}
      onSave={async () => {
        await api.PUT('/api/settings/volunteers', { body: value }).then(unwrap)
        draft.discard()
        reload()
      }}
    >
      <MinutesField
        label="Reminder lead (minutes)"
        hint="How long before a shift its volunteers get a DM. Blank uses 60."
        value={value.reminder_lead_minutes ?? null}
        onChange={(n) => draft.set({ reminder_lead_minutes: n })}
      />
    </SettingsCard>
  )
}

export function Meetups() {
  const { data, error, reload } = useQuery('settings-meetups', () => api.GET('/api/settings/meetups').then(unwrap))
  const draft = useDraft(data)
  if (error && !data) return <LoadError error={error} onRetry={reload} />
  if (!draft.value) return <Spinner label="Loading meetup settings" />
  const value = draft.value
  return (
    <SettingsCard
      title="Meetups"
      icon="groups"
      subtitle="Who decides what gets listed, and how early subscribers get a reminder."
      dirty={draft.dirty}
      onDiscard={draft.discard}
      onSave={async () => {
        await api.PUT('/api/settings/meetups', { body: value }).then(unwrap)
        draft.discard()
        reload()
      }}
    >
      <SwitchRow
        label="Require staff approval"
        hint="When on, an attendee's meetup waits for staff before it's listed. When off, it's listed straight away and staff can still take it down."
        checked={value.require_approval}
        onChange={(on) => draft.patch({ require_approval: on })}
      />
      <hr className="settings-divider" />
      <MinutesField
        label="Reminder lead (minutes)"
        hint="How long before a meetup its subscribers get a DM. Blank uses 30."
        value={value.reminder_lead_minutes ?? null}
        onChange={(n) => draft.patch({ reminder_lead_minutes: n })}
      />
    </SettingsCard>
  )
}
