import { useState } from 'react'
import { api, unwrap, type Schemas } from '../api/client'
import { Chip, Dialog, Spinner } from '../components'
import { DialogError, message, usePlatformQuery } from './parts'
import { LoadError } from './LoadError'

type Community = Schemas['CommunityResponse']
type Feature = Schemas['CommunityFeatureResponse']

function overrideValue(f: Feature): string {
  return f.override === true ? 'on' : f.override === false ? 'off' : ''
}

/** A community's tier, and per-feature overrides on top of it. */
export function CommunityFeaturesDialog({ community, onClose }: { community: Community; onClose: () => void }) {
  const path = { tenant_id: community.id }
  const features = usePlatformQuery(`community/${community.id}/features`, () =>
    api.GET('/api/platform/communities/{tenant_id}/features', { params: { path } }).then(unwrap),
  )
  const groups = usePlatformQuery('feature-groups', () => api.GET('/api/platform/feature-groups').then(unwrap))
  const [rows, setRows] = useState<Feature[] | null>(null)
  const [group, setGroup] = useState<number | null>(community.feature_group_id ?? null)
  const [error, setError] = useState<string | null>(null)
  const shown = rows ?? features.data ?? []

  async function pickGroup(value: string) {
    const id = value ? Number(value) : null
    setError(null)
    try {
      setRows(await api.PUT('/api/platform/communities/{tenant_id}/feature-group', { params: { path }, body: { group_id: id } }).then(unwrap))
      setGroup(id)
    } catch (err) {
      setError(message(err))
    }
  }

  async function override(flag: string, value: string) {
    setError(null)
    try {
      setRows(
        await api
          .PUT('/api/platform/communities/{tenant_id}/features/{flag}', {
            params: { path: { ...path, flag } },
            body: { available: value === 'on' ? true : value === 'off' ? false : null },
          })
          .then(unwrap),
      )
    } catch (err) {
      setError(message(err))
    }
  }

  const loadError = features.error ?? groups.error
  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={`Features for ${community.name}`}
      description="The group decides what's available; an override forces one feature on or off. The community's staff still choose whether to use what's available."
      size="lg"
    >
      <div className="pf-form">
        <DialogError error={error} />
        {loadError && !features.data ? (
          <LoadError error={loadError} reload={features.reload} />
        ) : !features.data || !groups.data ? (
          <Spinner label="Loading features" />
        ) : (
          <>
            <label className="field">
              <span className="field__label">Feature group</span>
              <select className="input" value={group ?? ''} onChange={(e) => void pickGroup(e.target.value)}>
                <option value="">Default ({groups.data.find((g) => g.is_default)?.name ?? 'none'})</option>
                {groups.data.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name}
                  </option>
                ))}
              </select>
            </label>
            <ul className="pf-list" aria-label="Features">
              {shown.map((f) => (
                <li key={f.flag}>
                  <span className="pf-list__main pf-stack">
                    <span className="pf-name">
                      {f.label}
                      {f.live ? <Chip tone="ok">On</Chip> : f.available ? <Chip tone="pending">Available, switched off</Chip> : <Chip>Off</Chip>}
                    </span>
                    <span className="pf-muted">{f.description}</span>
                  </span>
                  <select
                    className="input pf-list__control"
                    aria-label={`${f.label} override`}
                    value={overrideValue(f)}
                    onChange={(e) => void override(f.flag, e.target.value)}
                  >
                    <option value="">Group ({f.group_available ? 'on' : 'off'})</option>
                    <option value="on">Force on</option>
                    <option value="off">Force off</option>
                  </select>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </Dialog>
  )
}
