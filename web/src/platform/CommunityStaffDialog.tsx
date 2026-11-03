import { useState } from 'react'
import { api, unwrap, type Schemas } from '../api/client'
import { Button, Dialog, EmptyState, SearchField, Spinner } from '../components'
import { DialogError, message, usePlatformQuery } from './parts'
import { LoadError } from './LoadError'

type Community = Schemas['CommunityResponse']
type Staff = Schemas['StaffMemberResponse']

function who(u: { display_name?: string | null; username: string }): string {
  return u.display_name || u.username
}

/** Who holds Staff in a community, and granting it to the first (or another) person. */
export function CommunityStaffDialog({ community, onClose }: { community: Community; onClose: () => void }) {
  const detail = usePlatformQuery(`community/${community.id}`, () =>
    api.GET('/api/platform/communities/{tenant_id}', { params: { path: { tenant_id: community.id } } }).then(unwrap),
  )
  const [staff, setStaff] = useState<Staff[] | null>(null)
  const [q, setQ] = useState('')
  const [results, setResults] = useState<Schemas['PlatformUserResponse'][] | null>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const current = staff ?? detail.data?.staff ?? []

  async function search(value: string) {
    setQ(value)
    if (value.trim().length < 2) {
      setResults(null)
      return
    }
    try {
      setResults(await api.GET('/api/platform/users', { params: { query: { q: value.trim(), limit: 10 } } }).then(unwrap))
    } catch (err) {
      setError(message(err))
    }
  }

  async function grant(userId: number) {
    setBusy(userId)
    setError(null)
    try {
      setStaff(
        await api
          .POST('/api/platform/communities/{tenant_id}/staff', {
            params: { path: { tenant_id: community.id } },
            body: { user_id: userId },
          })
          .then(unwrap),
      )
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={`Staff for ${community.name}`}
      description="Give someone Staff here, usually the community's first organiser. They can hand out every other role themselves."
    >
      <div className="pf-form">
        <DialogError error={error} />
        {detail.error && !detail.data ? (
          <LoadError error={detail.error} reload={detail.reload} />
        ) : detail.loading && !detail.data ? (
          <Spinner label="Loading staff" />
        ) : current.length === 0 ? (
          <EmptyState icon="admin_panel_settings" title="No staff yet" />
        ) : (
          <ul className="pf-list" aria-label="Current staff">
            {current.map((u) => (
              <li key={u.id}>
                <span className="pf-list__main">{who(u)}</span>
                <span className="pf-muted">{u.username}</span>
              </li>
            ))}
          </ul>
        )}
        <SearchField value={q} onChange={(v) => void search(v)} label="Find someone by name" />
        {results && (
          <ul className="pf-list" aria-label="Matching accounts">
            {results.length === 0 && <li className="pf-muted">Nobody matches that.</li>}
            {results.map((u) => {
              const already = current.some((s) => s.id === u.id)
              return (
                <li key={u.id}>
                  <span className="pf-list__main pf-stack">
                    <span>{who(u)}</span>
                    <span className="pf-muted">{u.username}</span>
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={already}
                    busy={busy === u.id}
                    busyLabel="Granting…"
                    onClick={() => void grant(u.id)}
                    aria-label={already ? `${who(u)} is already staff` : `Make ${who(u)} staff`}
                  >
                    {already ? 'Staff' : 'Make staff'}
                  </Button>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </Dialog>
  )
}
