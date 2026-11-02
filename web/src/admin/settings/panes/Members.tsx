import { useId } from 'react'
import { Link } from 'react-router-dom'
import { api, unwrap } from '../../../api/client'
import { useQuery } from '../../../api/useQuery'
import { Icon, Spinner } from '../../../components'
import { LoadError, RadioRows, SettingsCard, SwitchRow, useAdmin, useDraft } from '../../kit'

function Joining() {
  const { adminBase } = useAdmin()
  const inviteId = useId()
  const { data, error, reload } = useQuery('settings-joining', () => api.GET('/api/settings/joining').then(unwrap))
  const saved = data && { join_mode: data.join_mode, discord_invite_url: data.discord_invite_url ?? '' }
  const draft = useDraft(saved)
  if (error && !data) return <LoadError error={error} onRetry={reload} />
  if (!data || !draft.value) return <Spinner label="Loading join settings" />
  const value = draft.value
  return (
    <SettingsCard
      title="Joining this community"
      icon="meeting_room"
      subtitle="Who becomes a member, and what the join page shows everyone else."
      dirty={draft.dirty}
      onDiscard={draft.discard}
      onSave={async () => {
        await api.PUT('/api/settings/joining', { body: value }).then(unwrap)
        draft.discard()
        reload()
      }}
    >
      <RadioRows
        label="Who can join"
        name="join-mode"
        value={value.join_mode}
        options={data.modes.map((m) => ({
          value: m.value,
          label: m.label,
          hint: m.value === value.join_mode ? m.help : undefined,
        }))}
        onChange={(mode) => draft.patch({ join_mode: mode })}
      />
      <Link to={`${adminBase}/members`} className="settings-link">
        <Icon name="manage_accounts" size={18} />
        Open the join queue
      </Link>
      <hr className="settings-divider" />
      <div className="field">
        <label className="field__label" htmlFor={inviteId}>
          Discord invite link
        </label>
        <input
          id={inviteId}
          className="input"
          type="url"
          inputMode="url"
          placeholder="https://discord.gg/abc123"
          value={value.discord_invite_url}
          aria-describedby={`${inviteId}-hint`}
          onChange={(e) => draft.patch({ discord_invite_url: e.target.value })}
        />
        <span className="field__hint" id={`${inviteId}-hint`}>
          Shown on the join page. Use one that doesn't expire.
        </span>
      </div>
    </SettingsCard>
  )
}

function ProfileReminders() {
  const { data, error, reload } = useQuery('settings-reminders', () => api.GET('/api/settings/profile-reminders').then(unwrap))
  const draft = useDraft(data?.reminders)
  if (error && !data) return <LoadError error={error} onRetry={reload} />
  if (!data || !draft.value) return <Spinner label="Loading profile reminders" />
  const rows = draft.value
  const update = (check: string, part: Partial<(typeof rows)[number]>) =>
    draft.set(rows.map((r) => (r.check === check ? { ...r, ...part } : r)))
  return (
    <SettingsCard
      title="Profile reminders"
      icon="person_check"
      subtitle="What members are asked to finish. Anyone who hasn't sees a banner on every page with a button to the right spot."
      dirty={draft.dirty}
      onDiscard={draft.discard}
      onSave={async () => {
        const rules = rows
          .filter((r) => r.enabled)
          .map((r) => ({ check: r.check, dismissible: r.dismissible, note: r.note ?? '' }))
        await api.PUT('/api/settings/profile-reminders', { body: { rules } }).then(unwrap)
        draft.discard()
        reload()
      }}
    >
      {rows.map((r, i) => (
        <div key={r.check} className="settings-stack settings-reminder">
          {i > 0 && <hr className="settings-divider" />}
          <SwitchRow
            label={r.label}
            hint={r.unavailable ? `${r.unavailable} Members won't see this one even if it's on.` : `Banner: "${r.prompt}"`}
            checked={r.enabled}
            onChange={(on) => update(r.check, { enabled: on })}
          />
          {r.enabled && (
            <div className="settings-reminder__more">
              <SwitchRow
                label="Members can dismiss it"
                hint="Off means the banner stays until they actually do it."
                checked={r.dismissible}
                onChange={(on) => update(r.check, { dismissible: on })}
              />
              <label className="field">
                <span className="field__label">Note on the banner (optional)</span>
                <input
                  className="input"
                  maxLength={data.max_note_length}
                  placeholder="e.g. Required for the Season 4 bracket."
                  value={r.note ?? ''}
                  onChange={(e) => update(r.check, { note: e.target.value })}
                />
              </label>
            </div>
          )}
        </div>
      ))}
    </SettingsCard>
  )
}

export function Members() {
  return (
    <>
      <Joining />
      <ProfileReminders />
    </>
  )
}
