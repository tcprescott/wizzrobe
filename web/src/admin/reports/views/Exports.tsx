import { useEffect, useState } from 'react'
import { api, unwrap, type Schemas } from '../../../api/client'
import { Banner, Button, Chip, Panel, type Tone } from '../../../components'
import { formatDayTime } from '../../../lib/time'
import { rangeLabel } from '../range'
import type { ReportProps } from '../types'
import { useReport } from '../useReport'
import { Loaded } from './Loading'

type Export = Schemas['ExportInfo']

const STATUS: Record<string, { label: string; tone: Tone }> = {
  queued: { label: 'Queued', tone: 'info' },
  running: { label: 'Building', tone: 'info' },
  ready: { label: 'Ready', tone: 'ok' },
  failed: { label: 'Failed', tone: 'cancelled' },
}

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

async function download(item: Export, format: 'file' | 'json') {
  const blob = (await api
    .GET('/api/exports/{export_id}/download', {
      params: { path: { export_id: item.id }, query: { format } },
      parseAs: 'blob',
    })
    .then(unwrap)) as unknown as Blob
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = format === 'json' ? `${(item.filename ?? `export-${item.id}`).replace(/\.\w+$/, '')}.json` : (item.filename ?? `export-${item.id}`)
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

/** Ask for a tournament or volunteer export, then download it here when it's built. */
export function Exports({ menu, range, bounds, zone }: ReportProps) {
  const tournaments = useReport(['exportable'], () => api.GET('/api/admin/reports/exportable-tournaments').then(unwrap))
  const mine = useReport(['exports'], () => api.GET('/api/exports').then(unwrap))
  const [picked, setPicked] = useState<Set<number> | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const volunteers = menu.reports.some((r) => r.key === 'volunteers')

  const building = mine.data?.items.some((e) => e.status === 'queued' || e.status === 'running')
  useEffect(() => {
    if (!building) return
    const t = setInterval(mine.reload, 3000)
    return () => clearInterval(t)
  }, [building, mine.reload])

  const chosen = picked ?? new Set((tournaments.data ?? []).filter((t) => t.active && !t.test).map((t) => t.id))

  async function run(what: string, call: () => Promise<unknown>) {
    setBusy(what)
    setError(null)
    try {
      await call()
      mine.reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <>
      {error && <Banner tone="error">{error}</Banner>}
      <Panel title="Tournaments">
        <Loaded query={tournaments} label="tournaments">
          {(list) =>
            list.length === 0 ? (
              <p className="report-note">No tournaments you can export.</p>
            ) : (
              <fieldset className="report-picks">
                <legend className="visually-hidden">Tournaments to export</legend>
                {list.map((t) => (
                  <label key={t.id} className="report-pick">
                    <input
                      type="checkbox"
                      checked={chosen.has(t.id)}
                      onChange={(e) => {
                        const next = new Set(chosen)
                        if (e.target.checked) next.add(t.id)
                        else next.delete(t.id)
                        setPicked(next)
                      }}
                    />
                    <span className="report-pick__text">
                      <b>{t.name}</b>
                      <span className="report-note">
                        {t.players} players · {t.finished} of {t.matches} matches finished
                        {t.bracket_site ? ` · ${t.bracket_site}` : ''}
                      </span>
                    </span>
                    {!t.active && <Chip>Inactive</Chip>}
                    {t.test && <Chip tone="info">Test</Chip>}
                  </label>
                ))}
              </fieldset>
            )
          }
        </Loaded>
        <div className="report-actions">
          <Button
            icon="download"
            disabled={chosen.size === 0}
            busy={busy === 'tournaments'}
            busyLabel="Asking…"
            onClick={() =>
              void run('tournaments', () =>
                api.POST('/api/exports/tournaments', { body: { tournament_ids: [...chosen] } }).then(unwrap),
              )
            }
          >
            Export {chosen.size === 1 ? '1 tournament' : `${chosen.size} tournaments`}
          </Button>
        </div>
      </Panel>
      {volunteers && (
        <Panel title="Volunteers">
          <p className="report-note">Roster, availability, positions, shifts and the schedule grid for {rangeLabel(range)}.</p>
          <div className="report-actions">
            <Button
              variant="outline"
              icon="download"
              busy={busy === 'volunteers'}
              busyLabel="Asking…"
              onClick={() =>
                void run('volunteers', () => api.POST('/api/exports/volunteers', { body: { start: bounds.start, end: bounds.end } }).then(unwrap))
              }
            >
              Export volunteer data
            </Button>
          </div>
        </Panel>
      )}
      <Panel title="Your exports">
        <Loaded query={mine} label="your exports">
          {({ items }) =>
            items.length === 0 ? (
              <p className="report-note">Nothing exported in the last 3 days.</p>
            ) : (
              <ul className="report-rows">
                {items.map((e) => {
                  const status = STATUS[e.status] ?? { label: e.status, tone: 'neutral' as Tone }
                  return (
                    <li key={e.id} className="report-row">
                      <span className="report-row__grow">
                        <b>{e.title}</b>
                        <span className="report-note">
                          {formatDayTime(e.created_at, zone)}
                          {e.status === 'running' && e.progress_total ? ` · ${e.progress_done} of ${e.progress_total}` : ''}
                          {e.status === 'ready' ? ` · ${size(e.size_bytes)}` : ''}
                          {e.error ? ` · ${e.error}` : ''}
                        </span>
                      </span>
                      <Chip tone={status.tone}>{status.label}</Chip>
                      {e.status === 'ready' && (
                        <span className="report-chips">
                          <Button size="sm" variant="outline" icon="download" onClick={() => void run(`dl${e.id}`, () => download(e, 'file'))}>
                            Download
                          </Button>
                          {e.has_json && (
                            <Button size="sm" variant="ghost" onClick={() => void run(`dl${e.id}`, () => download(e, 'json'))}>
                              JSON
                            </Button>
                          )}
                        </span>
                      )}
                    </li>
                  )
                })}
              </ul>
            )
          }
        </Loaded>
      </Panel>
    </>
  )
}
