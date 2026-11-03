import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { beginSignIn, loadTokens, onSessionChange, signOut } from './session'

interface AuthState {
  signedIn: boolean
  signIn: (returnTo?: string) => Promise<void>
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [signedIn, setSignedIn] = useState(() => loadTokens() !== null)
  useEffect(() => onSessionChange(setSignedIn), [])
  const value = useMemo<AuthState>(
    () => ({
      signedIn,
      signIn: (returnTo = '/') => beginSignIn(returnTo),
      signOut,
    }),
    [signedIn],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth needs an AuthProvider')
  return value
}

/** Signed-out visitors go to /sign-in and come back here afterwards. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { signedIn } = useAuth()
  const location = useLocation()
  if (!signedIn) {
    const returnTo = `${location.pathname}${location.search}`
    return <Navigate to={`/sign-in?next=${encodeURIComponent(returnTo)}`} replace />
  }
  return <>{children}</>
}
