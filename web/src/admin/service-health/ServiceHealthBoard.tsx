import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { Schemas } from '../../api/client'
import { Button, Chip, EmptyState, type Tone } from '../../components'
import { formatDayTime } from '../../lib/time'
import './service-health.css'

export type ProbeResult = Schemas['ProbeResultResponse']

export interface FixLink {
  label: string
  to: string
}

const STATUS: Record<string, { label: string; tone: Tone; rank: number }> = {
  down: { label: 'Down', tone: 'cancelled', rank: 0 },
  degraded: { label: 'Degraded', tone: 'pending', rank: 1 },
  credential_warning: { label: 'Credential warning', tone: 'pending', rank: 2 },
  unknown: { label: 'Unknown', tone: 'neutral', rank: 3 },
  mock: { label: 'Mock', tone: 'info', rank: 4 },
  healthy: { label: 'Healthy', tone: 'ok', rank: 5 },
}
const statusOf = (s: string) => STATUS[s] ?? STATUS.unknown!

const CATEGORY: Record<string, string> = {
  core: 'Core',
  racetime: 'Racetime',
  integrations: 'Integrations',
  infrastructure: 'Infrastructure',
  discord: 'Discord',
  seedgen: 'Seed generation',
  monitoring: 'Monitoring',
}

interface BoardProps {
  results: ProbeResult[]
  zone: string
  /** Where a row's problem gets fixed, when the viewer can fix it; null for none. */
  fixFor?: (result: ProbeResult) => FixLink | null
  /** A toolbar control beside the counts (the platform's "Refresh now"). */
  actions?: ReactNode
}

/**
 * The service health board, shared by Admin › Service Health and Platform ›
 * Service health: counts, then one card per service with problems first.
 */
export function ServiceHealthBoard({ results, zone, fixFor, actions }: BoardProps) {
  const sorted = [...results].sort(
    (a, b) => statusOf(a.status).rank - statusOf(b.status).rank || a.label.localeCompare(b.label),
  )
  const healthy = results.filter((r) => r.status === 'healthy' || r.status === 'mock').length
  const down = results.filter((r) => r.status === 'down').length
  const warn = results.length - healthy - down
  const latest = results.map((r) => r.checked_at).sort().at(-1)
  return (
    <div className="health">
      <div className="health-summary">
        <div className="health-count">
          <b>{healthy}</b>Healthy
        </div>
        <div className="health-count health-count--warn">
          <b>{warn}</b>
          {warn === 1 ? 'Warning' : 'Warnings'}
        </div>
        <div className="health-count health-count--down">
          <b>{down}</b>Down
        </div>
        <span className="health-summary__spacer" />
        {latest && <span className="health-muted">Checked {formatDayTime(latest, zone)}</span>}
        {actions}
      </div>
      {results.length === 0 ? (
        <EmptyState icon="monitor_heart" title="Nothing to report.">
          No outside services are set up yet.
        </EmptyState>
      ) : (
        <ul className="health-cards" aria-label="Services">
          {sorted.map((r) => {
            const s = statusOf(r.status)
            const fix = fixFor?.(r)
            return (
              <li key={`${r.key}-${r.label}`} className={`health-card health-card--${s.tone}`}>
                <div className="health-card__head">
                  <b>{r.label}</b>
                  <Chip tone={s.tone}>{s.label}</Chip>
                </div>
                <div className="health-muted">{CATEGORY[r.category] ?? r.category}</div>
                {r.message && <p className="health-card__msg">{r.message}</p>}
                <div className="health-muted">Checked {formatDayTime(r.checked_at, zone)}</div>
                {fix && (
                  <Button variant="outline" size="sm" asChild>
                    <Link to={fix.to}>{fix.label}</Link>
                  </Button>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
