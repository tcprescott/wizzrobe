import { useState } from 'react'
import { Navigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { Banner, Button } from '../components'
import './screens.css'

function safeNext(raw: string | null): string {
  return raw && raw.startsWith('/') && !raw.startsWith('//') ? raw : '/'
}

export function SignInScreen() {
  const { signedIn, signIn } = useAuth()
  const [params] = useSearchParams()
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const next = safeNext(params.get('next'))
  if (signedIn) return <Navigate to={next} replace />

  const start = () => {
    setBusy(true)
    setFailed(false)
    signIn(next).catch(() => {
      setBusy(false)
      setFailed(true)
    })
  }

  return (
    <main className="center-page">
      <div className="center-card">
        <span className="brand__mark" aria-hidden="true" />
        <h1>Sign in to Wizzrobe</h1>
        <p>Your matches, shifts and everything else for the event, on any device.</p>
        {failed && <Banner tone="error">Couldn't start sign-in. Check your connection and try again.</Banner>}
        <Button icon="login" onClick={start} busy={busy} busyLabel="Opening sign-in…">
          Sign in
        </Button>
      </div>
    </main>
  )
}
