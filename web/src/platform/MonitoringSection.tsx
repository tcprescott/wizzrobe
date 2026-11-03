import { useState } from 'react'
import { api, unwrap, type Schemas } from '../api/client'
import { Button, Chip, ConfirmDialog, FormDialog, PageHeader, Panel, PanelRow, Spinner } from '../components'
import { DataTable, type Column } from '../admin/kit'
import { LoadError } from './LoadError'
import { DialogError, message, useNotice, usePlatformQuery } from './parts'

type Settings = Schemas['BrowserMonitoringSettingsResponse']
type Rule = Schemas['IgnoreRuleResponse']

const WHERE: Record<string, string> = { message: 'Error message', url: 'Script URL' }

/** Whether people's browsers report errors to Sentry, and the known noise to drop. */
export function MonitoringSection() {
  const settings = usePlatformQuery('browser-monitoring', () => api.GET('/api/platform/browser-monitoring').then(unwrap))
  const rules = usePlatformQuery('browser-monitoring/rules', () => api.GET('/api/platform/browser-monitoring/rules').then(unwrap))
  const [current, setCurrent] = useState<Settings | null>(null)
  const [ruleRows, setRuleRows] = useState<Rule[] | null>(null)
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<Rule | null>(null)
  const { banner, run } = useNotice()
  const s = current ?? settings.data
  const shownRules = ruleRows ?? rules.data ?? []

  async function save(next: Partial<Settings>) {
    if (!s) return
    const body = { enabled: s.enabled, replay_on_error: s.replay_on_error, sample_rate: s.sample_rate, ...next }
    await run(async () => setCurrent(await api.PUT('/api/platform/browser-monitoring', { body }).then(unwrap)), 'Saved. Pages opened from now on use it.')
  }

  const columns: Column<Rule>[] = [
    {
      key: 'pattern',
      header: 'Ignores',
      primary: true,
      cell: (r) => (
        <span className="pf-name">
          <span className="pf-mono">{r.pattern}</span>
          {r.builtin && <Chip>Built in</Chip>}
        </span>
      ),
    },
    { key: 'match', header: 'Looks in', cell: (r) => WHERE[r.match] ?? r.match },
    { key: 'by', header: 'Added by', cell: (r) => r.created_by ?? <span className="pf-muted">Wizzrobe</span> },
  ]

  const error = settings.error ?? rules.error
  return (
    <>
      <PageHeader
        title="Monitoring"
        lede="Errors in people's browsers go to Sentry beside the server's. Turn it down here when it's noisy."
        actions={
          <Button icon="add" onClick={() => setAdding(true)}>
            Ignore an error
          </Button>
        }
      />
      {banner}
      {error && !s ? (
        <LoadError error={error} reload={settings.reload} />
      ) : !s || !rules.data ? (
        <Spinner label="Loading monitoring" />
      ) : (
        <>
          <Panel title="Browser errors">
            <PanelRow>
              <label className="pf-switch-row">
                <span className="pf-switch-row__text">
                  <span>Report browser errors</span>
                  <span className="pf-muted">Off, browsers send nothing to Sentry.</span>
                </span>
                <input type="checkbox" role="switch" className="switch" checked={s.enabled} onChange={(e) => void save({ enabled: e.target.checked })} />
              </label>
            </PanelRow>
            <PanelRow>
              <label className="pf-switch-row">
                <span className="pf-switch-row__text">
                  <span>Session replay on error</span>
                  <span className="pf-muted">A masked recording of the moments before each error. Text and inputs are hidden.</span>
                </span>
                <input
                  type="checkbox"
                  role="switch"
                  className="switch"
                  disabled={!s.enabled}
                  checked={s.replay_on_error}
                  onChange={(e) => void save({ replay_on_error: e.target.checked })}
                />
              </label>
            </PanelRow>
            <PanelRow>
              <label className="pf-switch-row">
                <span className="pf-switch-row__text">
                  <span>Share sent</span>
                  <span className="pf-muted">
                    {s.updated_by ? `Last changed by ${s.updated_by}.` : 'Never changed.'}
                  </span>
                </span>
                <select
                  className="input pf-list__control"
                  disabled={!s.enabled}
                  value={s.sample_rate}
                  onChange={(e) => void save({ sample_rate: Number(e.target.value) })}
                >
                  {s.sample_rates.map((r) => (
                    <option key={r} value={r}>
                      {Math.round(r * 100)}%
                    </option>
                  ))}
                </select>
              </label>
            </PanelRow>
          </Panel>
          <DataTable
            label="Ignore rules"
            columns={columns}
            rows={shownRules}
            rowKey={(r) => r.id ?? `builtin:${r.match}:${r.pattern}`}
            rowMenuLabel={(r) => `More for ${r.pattern}`}
            rowMenu={(r) =>
              r.builtin ? [] : [{ label: 'Stop ignoring…', icon: 'delete', destructive: true, onSelect: () => setRemoving(r) }]
            }
          />
        </>
      )}
      {adding && (
        <RuleDialog
          onClose={() => setAdding(false)}
          onSaved={(next) => {
            setRuleRows(next)
            setAdding(false)
          }}
        />
      )}
      {removing && (
        <ConfirmDialog
          open
          onOpenChange={(open) => !open && setRemoving(null)}
          title="Stop ignoring this error?"
          action="Stop ignoring"
          onConfirm={() =>
            run(
              async () =>
                setRuleRows(
                  await api.DELETE('/api/platform/browser-monitoring/rules/{rule_id}', { params: { path: { rule_id: removing.id! } } }).then(unwrap),
                ),
              'Rule removed.',
            )
          }
        >
          Browser errors containing “{removing.pattern}” reach Sentry again.
        </ConfirmDialog>
      )}
    </>
  )
}

function RuleDialog({ onClose, onSaved }: { onClose: () => void; onSaved: (rows: Rule[]) => void }) {
  const [pattern, setPattern] = useState('')
  const [match, setMatch] = useState('message')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return (
    <FormDialog
      open
      onOpenChange={(open) => !open && onClose()}
      title="Ignore a browser error"
      size="sm"
      primaryLabel="Ignore it"
      busy={busy}
      onPrimary={async () => {
        setBusy(true)
        setError(null)
        try {
          onSaved(await api.POST('/api/platform/browser-monitoring/rules', { body: { pattern, match } }).then(unwrap))
        } catch (err) {
          setError(message(err))
        } finally {
          setBusy(false)
        }
      }}
    >
      <div className="pf-form">
        <DialogError error={error} />
        <label className="field">
          <span className="field__label">Text to look for</span>
          <input className="input pf-mono" value={pattern} maxLength={200} onChange={(e) => setPattern(e.target.value)} />
          <span className="field__hint">A distinctive part of the error, at least 3 characters. Case doesn't matter.</span>
        </label>
        <label className="field">
          <span className="field__label">Look in</span>
          <select className="input" value={match} onChange={(e) => setMatch(e.target.value)}>
            <option value="message">The error message</option>
            <option value="url">A script URL in its stack</option>
          </select>
        </label>
      </div>
    </FormDialog>
  )
}
