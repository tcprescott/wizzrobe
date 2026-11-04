import type { Schemas } from '../api/client'

type Odds = Schemas['MysterySettingOdds']

/** A weightset's odds: each setting with every value it can roll and its chance, likeliest first. */
export function MysteryOdds({ odds }: { odds: Odds[] }) {
  if (odds.length === 0) return <p className="preset-muted">This weightset has no settings with odds.</p>
  return (
    <dl className="preset-settings preset-odds">
      {odds.map((o) => (
        <div key={o.setting}>
          <dt>{o.setting}</dt>
          <dd>
            {o.options.map((opt, i) => (
              <span key={opt.value} className={opt.percent === 0 ? 'preset-odds__never' : undefined}>
                {i > 0 && ' · '}
                {opt.value} <span className="preset-odds__pct">{formatPercent(opt.percent)}</span>
              </span>
            ))}
          </dd>
        </div>
      ))}
    </dl>
  )
}

function formatPercent(p: number): string {
  return `${Number.isInteger(p) ? p : p.toFixed(1)}%`
}
