import { useEffect, useState } from 'react'
import { api, unwrap, type Schemas } from '../api/client'
import { Chip, ConfirmDialog, PageHeader, SearchField, Spinner } from '../components'
import { DataTable, type Column } from '../admin/kit'
import { LoadError } from './LoadError'
import { usePlatformAccess } from './access'
import { Toolbar, useNotice, usePlatformQuery } from './parts'

type Account = Schemas['PlatformUserResponse']
type Filter = 'all' | 'password' | 'inactive'

const LIMIT = 200

function name(a: Account, me: number | undefined): string {
  const base = a.display_name || a.username
  return a.id === me ? `${base} (you)` : base
}

/** Every account on the platform: find one, see how it signs in and where it belongs, switch it off. */
export function UsersSection() {
  const me = usePlatformAccess().data?.user_id
  const [q, setQ] = useState('')
  const [term, setTerm] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [deactivating, setDeactivating] = useState<Account | null>(null)
  const { banner, run } = useNotice()

  useEffect(() => {
    const t = setTimeout(() => setTerm(q.trim()), 250)
    return () => clearTimeout(t)
  }, [q])

  const { data, error, loading, reload } = usePlatformQuery(`users?q=${term}&f=${filter}`, () =>
    api
      .GET('/api/platform/users', {
        params: {
          query: {
            q: term || undefined,
            has_password: filter === 'password' ? true : undefined,
            is_active: filter === 'inactive' ? false : undefined,
            limit: LIMIT,
          },
        },
      })
      .then(unwrap),
  )

  async function setActive(a: Account, active: boolean): Promise<boolean> {
    const ok = await run(
      () => api.PUT('/api/platform/users/{user_id}/active', { params: { path: { user_id: a.id } }, body: { is_active: active } }).then(unwrap),
      active ? `${name(a, me)} can sign in again.` : `${name(a, me)} is signed out everywhere and can't sign in.`,
    )
    if (ok) reload()
    return ok
  }

  const columns: Column<Account>[] = [
    {
      key: 'name',
      header: 'Account',
      primary: true,
      cell: (a) => (
        <span className="pf-name">
          {name(a, me)}
          {a.is_super_admin && <Chip tone="info">Super admin</Chip>}
          {!a.is_active && <Chip tone="cancelled">Deactivated</Chip>}
        </span>
      ),
    },
    {
      key: 'signin',
      header: 'Signs in with',
      cell: (a) => (
        <span className="pf-stack">
          {a.discord_id != null && <span>Discord · {a.username}</span>}
          {a.login_name && <span>Password · {a.login_name}</span>}
        </span>
      ),
    },
    {
      key: 'communities',
      header: 'Communities',
      cell: (a) => (a.communities.length ? a.communities.join(', ') : <span className="pf-muted">None</span>),
    },
  ]

  const rows = data ?? []
  return (
    <>
      <PageHeader title="Users" lede="Every account across Wizzrobe, in every community." />
      {banner}
      <Toolbar>
        <SearchField value={q} onChange={setQ} label="Search by name, Discord or sign-in name" />
        <label className="field field--inline">
          <span className="visually-hidden">Show</span>
          <select className="input" value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
            <option value="all">Everyone</option>
            <option value="password">With a password</option>
            <option value="inactive">Deactivated</option>
          </select>
        </label>
      </Toolbar>
      {error && !data ? (
        <LoadError error={error} reload={reload} />
      ) : loading && !data ? (
        <Spinner label="Loading accounts" />
      ) : (
        <>
          <DataTable
            label="Accounts"
            columns={columns}
            rows={rows}
            rowKey={(a) => a.id}
            rowMenuLabel={(a) => `More for ${name(a, me)}`}
            rowMenu={(a) =>
              a.is_active
                ? [{ label: 'Deactivate…', icon: 'person_off', destructive: true, disabled: a.id === me, onSelect: () => setDeactivating(a) }]
                : [{ label: 'Reactivate', icon: 'how_to_reg', onSelect: () => void setActive(a, true) }]
            }
            empty={{ icon: 'person', title: term ? 'Nobody matches that' : 'No accounts here' }}
          />
          {rows.length >= LIMIT && <p className="pf-muted">Showing the first {LIMIT}. Search to narrow it down.</p>}
        </>
      )}
      {deactivating && (
        <ConfirmDialog
          open
          onOpenChange={(open) => !open && setDeactivating(null)}
          title={`Deactivate ${name(deactivating, me)}?`}
          action="Deactivate"
          onConfirm={() => setActive(deactivating, false)}
        >
          They're signed out everywhere at once and can't sign in to any community until you reactivate them.
        </ConfirmDialog>
      )}
    </>
  )
}
