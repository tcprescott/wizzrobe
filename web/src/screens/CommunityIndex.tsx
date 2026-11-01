import { Link, Navigate } from 'react-router-dom'
import { useQuery } from '../api/useQuery'
import { Banner, Button, EmptyState, Icon, PageHeader, Panel, Spinner } from '../components'
import { LAST_COMMUNITY_KEY, loadMyCommunities } from '../community/CommunityProvider'
import { readStored } from '../lib/storage'
import './screens.css'

/** `/`: straight into your last (or only) community, else a list to pick from. */
export function CommunityIndex() {
  const { data, error, loading, reload } = useQuery('me/communities', loadMyCommunities)
  if (loading && !data) return <Spinner label="Loading your communities…" />
  if (error) {
    return (
      <main className="shell__content">
        <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
          {error.message}
        </Banner>
      </main>
    )
  }
  const communities = data ?? []
  const last = readStored(LAST_COMMUNITY_KEY)
  const pick = communities.find((c) => c.slug === last) ?? (communities.length === 1 ? communities[0] : undefined)
  if (pick) return <Navigate to={`/t/${pick.slug}/home`} replace />

  return (
    <main className="shell__content">
      <PageHeader title="Your communities" lede="Pick one to open. You can switch any time from the top bar." />
      <Panel>
        {communities.length === 0 ? (
          <EmptyState icon="groups" title="You haven't joined a community yet">
            Ask the community's staff for its join link.
          </EmptyState>
        ) : (
          communities.map((c) => (
            <Link key={c.slug} to={`/t/${c.slug}/home`} className="list-link">
              <span>
                {c.name}
                <span className="list-link__sub">{c.events.find((e) => e.is_default)?.name ?? 'No events yet'}</span>
              </span>
              <Icon name="chevron_right" />
            </Link>
          ))
        )}
      </Panel>
    </main>
  )
}
