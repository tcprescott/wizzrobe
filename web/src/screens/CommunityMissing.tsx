import { Link } from 'react-router-dom'
import { Banner, Button, Spinner } from '../components'
import './screens.css'

export function CommunityMissing({ loading, error, reload }: { loading: boolean; error?: Error; reload: () => void }) {
  if (loading) return <Spinner label="Loading…" />
  return (
    <main className="center-page">
      <div className="center-card">
        {error ? (
          <>
            <h1>Couldn't load this community</h1>
            <Banner tone="error">{error.message}</Banner>
            <Button onClick={reload}>Try again</Button>
          </>
        ) : (
          <>
            <h1>You're not in this community</h1>
            <p>It may have been renamed, or you haven't joined it yet.</p>
            <Button asChild>
              <Link to="/">Go to your communities</Link>
            </Button>
          </>
        )}
      </div>
    </main>
  )
}
