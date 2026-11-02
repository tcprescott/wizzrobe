import { api, unwrap, type Schemas } from '../../../api/client'
import { useQuery } from '../../../api/useQuery'
import { Icon, Spinner } from '../../../components'
import { LoadError, SettingsCard, SwitchRow, useDraft } from '../../kit'

type Feature = Schemas['FeatureRow']

function Category({ title, rows, onSaved }: { title: string; rows: Feature[]; onSaved: () => void }) {
  const saved = Object.fromEntries(rows.map((r) => [r.flag, r.enabled]))
  const draft = useDraft<Record<string, boolean>>(saved)
  const value = draft.value ?? saved
  return (
    <SettingsCard
      title={title}
      dirty={draft.dirty}
      onDiscard={draft.discard}
      onSave={async () => {
        for (const r of rows) {
          if (r.available && value[r.flag] !== r.enabled) {
            await api
              .PUT('/api/settings/features/{flag}', {
                params: { path: { flag: r.flag } },
                body: { enabled: Boolean(value[r.flag]) },
              })
              .then(unwrap)
          }
        }
        draft.discard()
        onSaved()
      }}
    >
      {rows.map((r) =>
        r.available ? (
          <SwitchRow
            key={r.flag}
            label={r.label}
            hint={r.description}
            checked={Boolean(value[r.flag])}
            onChange={(on) => draft.set({ ...value, [r.flag]: on })}
          />
        ) : (
          <div key={r.flag} className="settings-row settings-row--locked">
            <span className="settings-row__text">
              <span className="settings-row__title">{r.label}</span>
              <span className="settings-row__sub">{r.description}</span>
            </span>
            <span className="settings-locked">
              <Icon name="lock" size={18} />
              Not available
            </span>
          </div>
        ),
      )}
    </SettingsCard>
  )
}

export function Features() {
  const { data, error, reload } = useQuery('settings-features', () => api.GET('/api/settings/features').then(unwrap))
  if (error && !data) return <LoadError error={error} onRetry={reload} />
  if (!data) return <Spinner label="Loading features" />
  const categories: string[] = []
  for (const f of data.features) if (!categories.includes(f.category)) categories.push(f.category)
  return (
    <>
      <p className="settings-note">
        A platform admin decides which features your community can use{data.tier ? ` (your tier: ${data.tier})` : ''}. You decide
        whether each one is on.
      </p>
      {categories.map((c) => (
        <Category key={c} title={c} rows={data.features.filter((f) => f.category === c)} onSaved={reload} />
      ))}
    </>
  )
}
