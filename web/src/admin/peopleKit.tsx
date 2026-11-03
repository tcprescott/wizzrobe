import './people.css'
import { useState } from 'react'
import { useQuery } from '../api/useQuery'
import { useCommunity } from '../community/CommunityProvider'
import { useEventQuery } from '../me/data'

/** One admin read, keyed on the community and event so switching either reloads it. */
export function useAdminData<T>(name: string, load: () => Promise<T>) {
  const { community } = useCommunity()
  const { event } = useEventQuery()
  return useQuery(`admin/${name}@${community.slug}/${event ?? ''}`, load)
}

export function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

/** A round picture, or the person's first letter when there's no picture (or it 404s). */
export function Avatar({ name, url, size = 32 }: { name: string; url?: string | null; size?: number }) {
  const [broken, setBroken] = useState(false)
  const style = { width: size, height: size, fontSize: Math.round(size * 0.42) }
  if (url && !broken) {
    return <img className="admin-avatar" src={url} alt="" style={style} onError={() => setBroken(true)} />
  }
  return (
    <span className="admin-avatar" style={style} aria-hidden="true">
      {(name.trim()[0] ?? '?').toUpperCase()}
    </span>
  )
}

/** Run a write, tracking busy and error state for the button and the line under it. */
export function useAction() {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  async function run(key: string, work: () => Promise<unknown>): Promise<boolean> {
    setBusy(key)
    setError(null)
    try {
      await work()
      return true
    } catch (err) {
      setError(message(err))
      return false
    } finally {
      setBusy(null)
    }
  }
  return { busy, error, setError, run }
}
