import { useState } from 'react'
import { api, unwrap, type Schemas } from '../api/client'
import { Button, Chip, FormDialog, PageHeader, Spinner } from '../components'
import { DataTable, type Column } from '../admin/kit'
import { LoadError } from './LoadError'
import { DialogError, message, useNotice, usePlatformQuery } from './parts'

type App = Schemas['PlatformAppResponse']

const COLUMNS: Column<App>[] = [
  {
    key: 'name',
    header: 'App',
    primary: true,
    cell: (a) => (
      <span className="pf-name">
        {a.name}
        {a.first_party && <Chip tone="info">Wizzrobe</Chip>}
      </span>
    ),
  },
  { key: 'owner', header: 'Owner', cell: (a) => a.owner },
  { key: 'kind', header: 'Kind', cell: (a) => (a.kind === 'website' ? 'Website' : 'Device app') },
  { key: 'people', header: 'People', align: 'end', cell: (a) => a.people },
  { key: 'communities', header: 'Communities', align: 'end', cell: (a) => a.communities },
  {
    key: 'status',
    header: 'Status',
    cell: (a) =>
      a.disabled ? (
        <span className="pf-stack">
          <Chip tone="cancelled">Disabled</Chip>
          {a.disabled_reason && <span className="pf-muted">{a.disabled_reason}</span>}
        </span>
      ) : (
        <Chip tone="ok">Active</Chip>
      ),
  },
]

/** Apps people registered to sign in with Wizzrobe; disabling one stops it in every community. */
export function AppsSection() {
  const { data, error, loading, reload } = usePlatformQuery('apps', () => api.GET('/api/platform/apps').then(unwrap))
  const [disabling, setDisabling] = useState<App | null>(null)
  const [rows, setRows] = useState<App[] | null>(null)
  const { banner, run } = useNotice()
  const shown = rows ?? data ?? []

  return (
    <>
      <PageHeader
        title="Apps"
        lede="Apps people registered to sign in with Wizzrobe. Disabling one stops it in every community at once; enabling it again brings its connections back."
      />
      {banner}
      {error && !data ? (
        <LoadError error={error} reload={reload} />
      ) : loading && !data ? (
        <Spinner label="Loading apps" />
      ) : (
        <DataTable
          label="Apps"
          columns={COLUMNS}
          rows={shown}
          rowKey={(a) => a.id}
          rowAction={(a) =>
            a.disabled ? (
              <Button
                variant="outline"
                size="sm"
                aria-label={`Enable ${a.name}`}
                onClick={() =>
                  void run(
                    async () => setRows(await api.POST('/api/platform/apps/{app_id}/enable', { params: { path: { app_id: a.id } } }).then(unwrap)),
                    `${a.name} enabled.`,
                  )
                }
              >
                Enable
              </Button>
            ) : a.first_party ? null : (
              <Button variant="outline" size="sm" aria-label={`Disable ${a.name}`} onClick={() => setDisabling(a)}>
                Disable
              </Button>
            )
          }
          empty={{ icon: 'apps', title: 'Nobody has registered an app yet' }}
        />
      )}
      {disabling && (
        <DisableDialog
          app={disabling}
          onClose={() => setDisabling(null)}
          onDone={(next) => {
            setRows(next)
            setDisabling(null)
          }}
        />
      )}
    </>
  )
}

function DisableDialog({ app, onClose, onDone }: { app: App; onClose: () => void; onDone: (rows: App[]) => void }) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return (
    <FormDialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={`Disable ${app.name}?`}
      size="sm"
      primaryLabel="Disable"
      destructive
      busy={busy}
      onPrimary={async () => {
        setBusy(true)
        setError(null)
        try {
          onDone(await api.POST('/api/platform/apps/{app_id}/disable', { params: { path: { app_id: app.id } }, body: { reason } }).then(unwrap))
        } catch (err) {
          setError(message(err))
        } finally {
          setBusy(false)
        }
      }}
    >
      <div className="pf-form">
        <p className="confirm-dialog__text">
          It stops working in every community at once for the {app.people} {app.people === 1 ? 'person' : 'people'} who connected it. The owner sees the reason.
        </p>
        <DialogError error={error} />
        <label className="field">
          <span className="field__label">Reason</span>
          <input className="input" value={reason} maxLength={300} placeholder="e.g. Phishing" onChange={(e) => setReason(e.target.value)} />
        </label>
      </div>
    </FormDialog>
  )
}
