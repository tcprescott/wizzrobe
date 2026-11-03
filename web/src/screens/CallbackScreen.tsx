import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { completeSignIn, SignInError } from '../auth/session'
import { Banner, Button, Spinner } from '../components'
import './screens.css'

/** Where /oauth/authorize sends the browser back with a code. */
export function CallbackScreen() {
  const navigate = useNavigate()
  const [error, setError] = useState<string>()
  const ran = useRef(false)

  useEffect(() => {
    // Strict mode runs effects twice in dev; a code is single-use.
    if (ran.current) return
    ran.current = true
    completeSignIn(new URLSearchParams(window.location.search))
      .then((next) => navigate(next, { replace: true }))
      .catch((err: unknown) => {
        setError(err instanceof SignInError ? err.message : "Sign-in couldn't be finished. Try again.")
      })
  }, [navigate])

  return (
    <main className="center-page">
      <div className="center-card">
        {error ? (
          <>
            <h1>Sign-in didn't finish</h1>
            <Banner tone="error">{error}</Banner>
            <Button asChild>
              <Link to="/sign-in">Try again</Link>
            </Button>
          </>
        ) : (
          <>
            <h1 className="visually-hidden">Signing in</h1>
            <Spinner label="Signing you in…" />
          </>
        )}
      </div>
    </main>
  )
}
