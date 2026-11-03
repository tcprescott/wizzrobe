import type { ReactNode } from 'react'
import { Icon } from './Icon'

export type Tone = 'ok' | 'pending' | 'live' | 'neutral' | 'cancelled' | 'info'

/** Status is a tint, not a fill: soft background, thin border, status-coloured text. */
export function Chip({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`chip chip--${tone}`}>{children}</span>
}

export function CountBadge({ count, label }: { count: number; label: string }) {
  if (count <= 0) return null
  return (
    <span className="count-badge" aria-label={label}>
      {count > 99 ? '99+' : count}
    </span>
  )
}

/** A Patreon patron's badge beside their name: a filled heart and the label their tier earns here. */
export function PatronBadge({ label }: { label: string }) {
  return (
    <span className="patron-badge">
      <Icon name="favorite" filled size={14} />
      {label}
    </span>
  )
}
