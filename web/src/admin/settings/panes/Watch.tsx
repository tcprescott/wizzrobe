import { useEffect, useMemo, useState } from 'react'
import { api, unwrap } from '../../../api/client'
import { useQuery } from '../../../api/useQuery'
import { Banner, Button, Spinner } from '../../../components'
import { effectiveZone, formatTime } from '../../../lib/time'
import { LoadError, MinutesField, RadioRows, SettingsCard, SwitchRow, useDraft } from '../../kit'

const MODES = [
  { value: 'live', label: 'Live', hint: 'Updates the moment a match changes. Each viewer holds a connection.' },
  { value: 'static', label: 'Static', hint: 'One shared copy that updates itself every minute. The player keeps playing.' },
]

function Stat({ value, label }: { value: number; label: string }) {
  return (
    <div className="settings-stat">
      <span className="settings-stat__num">{value}</span>
      <span className="settings-stat__label">{label}</span>
    </div>
  )
}

export function Watch() {
  const { data, error, reload } = useQuery('settings-watch', () => api.GET('/api/settings/watch').then(unwrap))
  const [clearing, setClearing] = useState(false)
  useEffect(() => {
    const timer = window.setInterval(reload, 5000)
    return () => window.clearInterval(timer)
  }, [reload])
  const saved = useMemo(
    () =>
      data && {
        mode: data.mode,
        static_signed_out: data.static_signed_out,
        auto_static: data.auto_static,
        threshold: data.threshold,
      },
    [data],
  )
  const draft = useDraft(saved)
  if (error && !data) return <LoadError error={error} onRetry={reload} />
  if (!data || !draft.value) return <Spinner label="Loading Watch page settings" />
  const value = draft.value
  const a = data.audience
  const zone = effectiveZone(null)

  async function backToLive() {
    setClearing(true)
    try {
      await api.POST('/api/settings/watch/back-to-live').then(unwrap)
      reload()
    } finally {
      setClearing(false)
    }
  }

  return (
    <>
      <section className="panel settings-card" aria-labelledby="watch-now">
        <header className="settings-card__head">
          <div className="settings-card__heading">
            <h2 className="settings-card__title" id="watch-now">
              Right now
            </h2>
            <p className="settings-card__sub">Who is on the Watch page at /live. Updates every few seconds.</p>
          </div>
        </header>
        <div className="settings-card__body">
          {a.tripped && (
            <Banner
              tone="warning"
              action={
                <Button variant="outline" busy={clearing} busyLabel="Switching…" onClick={() => void backToLive()}>
                  Back to live now
                </Button>
              }
            >
              Switched to static{a.tripped_at ? ` at ${formatTime(a.tripped_at, zone)}` : ''}: people arrived faster than the app
              lets them in. It goes back to live after ten minutes without a rush.
            </Banner>
          )}
          <div className="settings-stats">
            <Stat value={a.live_viewers} label="Watching live" />
            <Stat value={a.static_readers} label="Reading the static page" />
            <Stat
              value={a.arrivals_last_minute}
              label={`Arrived in the last minute (the app takes about ${a.admissions_per_minute})`}
            />
            <Stat value={a.peak} label={`Most at once${a.peak_at ? ` at ${formatTime(a.peak_at, zone)}` : ''}`} />
          </div>
        </div>
      </section>
      <SettingsCard
        title="Mode"
        icon="tune"
        subtitle="Live suits a normal day. Static has no limit on how many people can read it."
        dirty={draft.dirty}
        onDiscard={draft.discard}
        onSave={async () => {
          await api.PUT('/api/settings/watch', { body: value }).then(unwrap)
          draft.discard()
          reload()
        }}
      >
        <RadioRows label="Mode" name="watch-mode" value={value.mode} options={MODES} onChange={(mode) => draft.patch({ mode })} />
        <SwitchRow
          label="Signed-out visitors always get the static page"
          hint="Saves the app a connection per anonymous viewer, but they only see changes once a minute. Signed-in people stay live."
          checked={value.static_signed_out}
          onChange={(on) => draft.patch({ static_signed_out: on })}
        />
        <hr className="settings-divider" />
        <SwitchRow
          label="Switch to static on its own during a rush"
          hint="New arrivals get the static page until ten minutes pass without a rush. People already watching stay live."
          checked={value.auto_static}
          onChange={(on) => draft.patch({ auto_static: on })}
        />
        <MinutesField
          label="Arrivals a minute that count as a rush"
          hint="Past this many arrivals in a minute, the switch above kicks in."
          value={value.threshold}
          onChange={(n) => draft.patch({ threshold: n ?? 0 })}
        />
      </SettingsCard>
    </>
  )
}
