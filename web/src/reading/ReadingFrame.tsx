import type { ReactNode } from 'react'
import { Outlet, useLocation, useParams } from 'react-router-dom'
import { setCommunity } from '../api/client'
import { RequireAuth, useAuth } from '../auth/AuthProvider'
import { CommunityProvider } from '../community/CommunityProvider'
import { Button } from '../components'
import { CommunityMissing } from '../screens/CommunityMissing'
import { isReadingPath } from './routes'

/**
 * The frame around every /t/<slug>/… screen. A member gets the app's shell.
 * The reading pages open for anyone, as on the website: someone signed out,
 * or signed in but not a member here, reads them in a plain frame with a way
 * to sign in. Every other screen still needs a sign-in and membership.
 */
export function ReadingFrame({ children }: { children: ReactNode }) {
  const { signedIn } = useAuth()
  const reading = isReadingPath(useLocation().pathname)
  if (!signedIn && reading) return <PublicFrame />
  return (
    <RequireAuth>
      <CommunityProvider
        fallback={(state) => (reading && !state.loading && !state.error ? <PublicFrame /> : <CommunityMissing {...state} />)}
      >
        {children}
      </CommunityProvider>
    </RequireAuth>
  )
}

function PublicFrame() {
  const { community = '' } = useParams()
  const { signedIn, signIn } = useAuth()
  const { pathname, search } = useLocation()
  setCommunity(community)
  return (
    <div className="public-frame">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="public-frame__bar">
        <span className="brand">
          <span className="brand__mark" aria-hidden="true" />
          <span className="brand__name">Wizzrobe</span>
        </span>
        {!signedIn && (
          <Button variant="outline" size="sm" icon="login" onClick={() => void signIn(`${pathname}${search}`)}>
            Sign in
          </Button>
        )}
      </header>
      <main id="main" className="public-frame__content" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  )
}
