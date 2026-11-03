import { useState } from 'react'
import { api, unwrap, type Schemas } from '../api/client'
import { Button, Chip, FormDialog, PageHeader, Spinner } from '../components'
import { DataTable, type Column } from '../admin/kit'
import { LoadError } from './LoadError'
import { DialogError, message, useNotice, usePlatformQuery } from './parts'

type Logger = Schemas['LogLevelResponse']

const LEVELS = ['TRACE', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']

function loggerName(l: Logger): string {
  return l.logger || 'root'
}

/** How much each part of the server writes to the container log, changed live. */
export function LoggingSection() {
  const { data, error, loading, reload } = usePlatformQuery('log-levels', () => api.GET('/api/platform/log-levels').then(unwrap))
  const [rows, setRows] = useState<Logger[] | null>(null)
  const [editing, setEditing] = useState<Logger | 'new' | null>(null)
  const { banner, run } = useNotice()
  const shown = rows ?? data ?? []

  const columns: Column<Logger>[] = [
    {
      key: 'logger',
      header: 'Logger',
      primary: true,
      cell: (l) => (
        <span className="pf-stack">
          <span className="pf-mono">{loggerName(l)}</span>
          {l.description && <span className="pf-muted">{l.description}</span>}
        </span>
      ),
    },
    {
      key: 'level',
      header: 'Level',
      cell: (l) => (
        <span className="pf-stack">
          <span className="pf-name">
            {l.effective}
            {l.override && <Chip tone="info">Set by hand</Chip>}
          </span>
          {l.override && l.updated_by && <span className="pf-muted">by {l.updated_by}</span>}
        </span>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title="Logging"
        lede="Changes take effect at once and last across restarts until reset. Sentry still gets INFO and up whatever you pick."
        actions={
          <Button icon="add" onClick={() => setEditing('new')}>
            Set a logger
          </Button>
        }
      />
      {banner}
      {error && !data ? (
        <LoadError error={error} reload={reload} />
      ) : loading && !data ? (
        <Spinner label="Loading loggers" />
      ) : (
        <DataTable
          label="Loggers"
          columns={columns}
          rows={shown}
          rowKey={(l) => l.logger}
          rowAction={(l) => (
            <Button variant="outline" size="sm" aria-label={`Change ${loggerName(l)}`} onClick={() => setEditing(l)}>
              Change
            </Button>
          )}
          rowMenuLabel={(l) => `More for ${loggerName(l)}`}
          rowMenu={(l) => [
            {
              label: 'Reset to inherited',
              icon: 'undo',
              disabled: !l.override,
              onSelect: () =>
                void run(
                  async () => setRows(await api.DELETE('/api/platform/log-levels', { params: { query: { logger: l.logger } } }).then(unwrap)),
                  `${loggerName(l)} is back to its parent's level.`,
                ),
            },
          ]}
          empty={{ icon: 'tune', title: 'No loggers' }}
        />
      )}
      {editing && (
        <LevelDialog
          logger={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={(next) => {
            setRows(next)
            setEditing(null)
          }}
        />
      )}
    </>
  )
}

function LevelDialog({ logger, onClose, onSaved }: { logger: Logger | null; onClose: () => void; onSaved: (rows: Logger[]) => void }) {
  const [name, setName] = useState(logger?.logger ?? '')
  const [level, setLevel] = useState(logger?.override ?? logger?.effective ?? 'INFO')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return (
    <FormDialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={logger ? `Level for ${loggerName(logger)}` : 'Set a logger level'}
      size="sm"
      primaryLabel="Apply"
      busy={busy}
      onPrimary={async () => {
        setBusy(true)
        setError(null)
        try {
          onSaved(await api.PUT('/api/platform/log-levels', { body: { logger: name, level } }).then(unwrap))
        } catch (err) {
          setError(message(err))
        } finally {
          setBusy(false)
        }
      }}
    >
      <div className="pf-form">
        <DialogError error={error} />
        {!logger && (
          <label className="field">
            <span className="field__label">Logger</span>
            <input className="input pf-mono" value={name} placeholder="e.g. discordbot.dm" onChange={(e) => setName(e.target.value)} />
            <span className="field__hint">Dotted Python logger name. Empty is the root logger.</span>
          </label>
        )}
        <label className="field">
          <span className="field__label">Level</span>
          <select className="input" value={level} onChange={(e) => setLevel(e.target.value)}>
            {LEVELS.map((l) => (
              <option key={l}>{l}</option>
            ))}
          </select>
        </label>
      </div>
    </FormDialog>
  )
}
