import { Button, Chip, Icon } from '../components'
import { useReadingBase, websiteUrl, type Tournament } from './data'

function closes(iso: string, zone: string): string {
  return new Intl.DateTimeFormat(undefined, {
    weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit', timeZone: zone,
  }).format(new Date(iso))
}

function money(amount: string | number): string {
  return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(Number(amount))
}

/** The handbook's live tournament list, drawn where an article says `:::tournaments`. */
export function TournamentList({ rows, zone }: { rows: Tournament[]; zone: string }) {
  const { community } = useReadingBase()
  if (!rows.length) return <p className="muted">No tournaments have been announced yet.</p>
  return (
    <ul className="handbook-tournaments">
      {rows.map((t) => {
        const status =
          t.signups === 'closed'
            ? 'sign-ups closed'
            : t.signups === 'open' && t.signups_close_at
              ? `sign-ups close ${closes(t.signups_close_at, zone)}`
              : ''
        const meta = [t.format, status].filter(Boolean).join(' · ')
        const bracket = t.bracket_url.startsWith('/') ? websiteUrl(t.bracket_url, community) : t.bracket_url
        return (
          <li key={t.id} className="handbook-tournament">
            <div className="handbook-tournament__text">
              <span className="handbook-tournament__name">
                {t.name}
                {t.prize_total != null && (
                  <Chip tone="ok">
                    <Icon name="payments" size={14} />
                    {money(t.prize_total)} prize pool
                  </Chip>
                )}
              </span>
              {meta && <span className="handbook-tournament__meta">{meta}</span>}
            </div>
            <div className="handbook-tournament__actions">
              {t.rules_url && (
                <a href={t.rules_url} target="_blank" rel="noopener noreferrer" aria-label={`${t.name} rules (opens in a new tab)`}>
                  Rules
                </a>
              )}
              {bracket && (
                <a href={bracket} aria-label={`${t.name} bracket`}>
                  Bracket
                </a>
              )}
              {t.signups === 'open' && (
                <Button size="sm" asChild>
                  <a href={websiteUrl('/home/tournaments', community)} aria-label={`Sign up for ${t.name}`}>
                    Sign up
                  </a>
                </Button>
              )}
            </div>
          </li>
        )
      })}
    </ul>
  )
}
