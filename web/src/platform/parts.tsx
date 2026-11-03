import { useState, type ReactNode } from 'react'
import { ApiError, errorMessage } from '../api/client'
import { useQuery } from '../api/useQuery'
import { Banner } from '../components'

export function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

/** For writes that answer 204: throw the server's message on failure. */
export function done(result: { error?: unknown; response: Response }): void {
  if (!result.response.ok) throw new ApiError(errorMessage(result.error, result.response.status), result.response.status)
}

/** Platform reads name no community, so one key per read is enough. */
export function usePlatformQuery<T>(key: string, load: () => Promise<T>) {
  return useQuery(`platform/${key}`, load)
}

export interface Notice {
  tone: 'info' | 'error'
  text: string
}

/** The line under a section's header saying what the last action did. */
export function useNotice() {
  const [notice, setNotice] = useState<Notice | null>(null)
  const banner = notice ? <Banner tone={notice.tone}>{notice.text}</Banner> : null
  /** Run a write; on success show `ok` (if any) and return true, on failure show why. */
  async function run(fn: () => Promise<unknown>, ok?: string): Promise<boolean> {
    try {
      await fn()
      setNotice(ok ? { tone: 'info', text: ok } : null)
      return true
    } catch (err) {
      setNotice({ tone: 'error', text: message(err) })
      return false
    }
  }
  return { banner, run, setNotice }
}

export function Toolbar({ children }: { children: ReactNode }) {
  return <div className="pf-toolbar">{children}</div>
}

/** A dialog form's error, shown inside the dialog so it isn't hidden behind it. */
export function DialogError({ error }: { error: string | null }) {
  return error ? <Banner tone="error">{error}</Banner> : null
}
