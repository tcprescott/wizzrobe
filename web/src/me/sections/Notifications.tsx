import { useEffect, useState } from 'react'
import { api, unwrap, type Schemas } from '../../api/client'
import { Banner, Button, FormDialog, Icon, Panel, PanelRow, RowMenu, Spinner } from '../../components'
import { currentEndpoint, pushUnavailable, subscribeDevice } from '../../lib/push'
import { message, useSection, type Channels } from '../data'
import { useProfile } from '../MeLayout'

type Mine = Schemas['MyNotificationsResponse']
type Row = Schemas['MyNotificationResponse']
type Mode = Row['mode']

const PRESETS: { value: Mine['preset']; title: string; blurb: string }[] = [
  { value: 'everything', title: 'Everything', blurb: 'Every message your community sends, the way it sends it.' },
  {
    value: 'needs_me',
    title: 'Only what needs me',
    blurb: 'Pings when you have something to do: acknowledge a match, pick a time, take a shift. The rest arrives silently.',
  },
  {
    value: 'quiet',
    title: 'Quiet',
    blurb: "Only what can't wait pings: stage calls, race rooms, shift starts. Nothing about matches you watch or follow.",
  },
]

const MODES: { value: Mode; label: string }[] = [
  { value: 'off', label: 'Off' },
  { value: 'silent', label: 'Silent' },
  { value: 'ping', label: 'Ping' },
]

const LEVELS: { value: string; label: string }[] = [
  { value: 'none', label: 'Not following' },
  { value: 'streamed', label: 'Streamed matches' },
  { value: 'streamed_and_candidates', label: 'Streamed, plus possible streams' },
  { value: 'all', label: 'Every match' },
]

const ORDER: Record<string, number> = { off: 0, silent: 1, ping: 2 }

function deviceName(agent: string | null | undefined): string {
  const ua = agent ?? ''
  const browser = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'Browser'
  const os = /iPhone|iPad/.test(ua) ? 'iPhone or iPad' : /Android/.test(ua) ? 'Android' : /Mac OS X/.test(ua) ? 'Mac' : /Windows/.test(ua) ? 'Windows' : /Linux/.test(ua) ? 'Linux' : ''
  return os ? `${browser} on ${os}` : browser
}

function Switch({ id, checked, onChange, label, disabled }: { id: string; checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <input
      id={id}
      type="checkbox"
      role="switch"
      className="switch"
      aria-label={label}
      checked={checked}
      disabled={disabled}
      onChange={(e) => onChange(e.target.checked)}
    />
  )
}

/** Where notifications reach you, how much you hear, and the tournaments you follow. */
export function Notifications() {
  const { reload: reloadProfile } = useProfile()
  const channels = useSection('channels', () => api.GET('/api/me/channels').then(unwrap))
  const mine = useSection('messages', () => api.GET('/api/notifications/messages').then(unwrap))
  const follow = useSection('follow', () => api.GET('/api/me/followable-tournaments').then(unwrap))
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  async function run<T>(work: () => Promise<T>, after: () => void, ok = 'Saved.'): Promise<T | undefined> {
    setError(null)
    try {
      const out = await work()
      setSaved(ok)
      after()
      reloadProfile()
      return out
    } catch (err) {
      setError(message(err))
      return undefined
    }
  }

  return (
    <div className="me-stack">
      {error && (
        <Banner tone="error">
          {error}
        </Banner>
      )}
      {saved && !error && (
        <p className="me-saved" role="status">
          {saved}
        </p>
      )}
      {channels.data ? (
        <ChannelsPanel data={channels.data} run={run} reload={channels.reload} />
      ) : (
        <Spinner label="Loading your channels" />
      )}
      {mine.data ? <HearPanel mine={mine.data} run={run} reload={mine.reload} /> : <Spinner label="Loading your settings" />}
      {follow.data && (
        <Panel title="Tournaments you follow">
          <PanelRow>
            <p className="me-note">Hear about other people's matches too. You don't have to be playing.</p>
          </PanelRow>
          {follow.data.length === 0 && (
            <PanelRow>
              <p className="me-note">No tournaments are running right now.</p>
            </PanelRow>
          )}
          {follow.data.map((t) => (
            <PanelRow key={t.id}>
              <div className="me-row">
                <span className="me-row__text">
                  <span className="me-row__title">{t.name}</span>
                  {t.playing && <span className="me-row__sub">You're playing</span>}
                </span>
                <select
                  className="input"
                  style={{ width: 'auto', maxWidth: '100%' }}
                  aria-label={`Alerts for ${t.name}`}
                  value={t.level}
                  onChange={(e) => {
                    const level = e.target.value
                    void run(
                      () =>
                        api
                          .PUT('/api/notifications/preferences', { body: { tournament_id: t.id, match_notifications: level } })
                          .then(unwrap),
                      follow.reload,
                    )
                  }}
                >
                  {LEVELS.map((l) => (
                    <option key={l.value} value={l.value}>
                      {l.label}
                    </option>
                  ))}
                </select>
              </div>
            </PanelRow>
          ))}
        </Panel>
      )}
    </div>
  )
}

type Run = <T>(work: () => Promise<T>, after: () => void, ok?: string) => Promise<T | undefined>

function ChannelsPanel({ data, run, reload }: { data: Channels; run: Run; reload: () => void }) {
  const [thisDevice, setThisDevice] = useState<string | null>(null)
  const [unavailable, setUnavailable] = useState<string | null>(null)
  const [emailOpen, setEmailOpen] = useState(false)
  const [testOpen, setTestOpen] = useState(false)
  const [telegramUrl, setTelegramUrl] = useState<string | null>(null)

  useEffect(() => {
    if (!data.push_configured) return
    void pushUnavailable().then(setUnavailable)
    void currentEndpoint().then(setThisDevice)
  }, [data.push_configured])

  useEffect(() => {
    if (!telegramUrl || data.telegram_connected) return
    let left = 40
    const id = window.setInterval(() => {
      left -= 1
      reload()
      if (left <= 0) window.clearInterval(id)
    }, 3000)
    return () => window.clearInterval(id)
  }, [telegramUrl, data.telegram_connected, reload])

  const subscribed = thisDevice != null && data.devices.length > 0

  return (
    <Panel
      title="Where we reach you"
      actions={
        <Button variant="outline" icon="send" onClick={() => setTestOpen(true)}>
          Send a test…
        </Button>
      }
    >
      {data.discord && (
        <PanelRow>
          <div className="me-row">
            <Icon name="chat" />
            <label className="me-row__text" htmlFor="ch-discord">
              <span className="me-row__title">Discord DMs</span>
              <span className="me-row__sub">From the Wizzrobe bot</span>
            </label>
            <Switch
              id="ch-discord"
              label="Discord DMs"
              checked={data.dm_notifications}
              onChange={(v) => void run(() => api.PATCH('/api/users/me', { body: { dm_notifications: v } }).then(unwrap), reload)}
            />
          </div>
        </PanelRow>
      )}
      {data.push_configured && (
        <PanelRow>
          <div className="me-row">
            <Icon name="mobile" />
            <span className="me-row__text">
              <span className="me-row__title">This device</span>
              <span className="me-row__sub">{subscribed ? 'Getting notifications' : unavailable ?? 'Not turned on'}</span>
            </span>
            {!subscribed && !unavailable && data.push_public_key && (
              <Button
                variant="outline"
                onClick={() =>
                  void run(
                    async () => {
                      const sub = await subscribeDevice(data.push_public_key as string)
                      await api.POST('/api/me/channels/devices', { body: sub }).then(unwrap)
                      setThisDevice(sub.endpoint)
                    },
                    reload,
                    'Notifications are on for this device.',
                  )
                }
              >
                Turn on
              </Button>
            )}
          </div>
        </PanelRow>
      )}
      {data.devices.map((d) => (
        <PanelRow key={d.id}>
          <div className="me-row">
            <Icon name="notifications" />
            <span className="me-row__text">
              <span className="me-row__title">{deviceName(d.user_agent)}</span>
              <span className="me-row__sub">Since {new Date(d.created_at).toLocaleDateString()}</span>
            </span>
            <RowMenu
              label={`More for ${deviceName(d.user_agent)}`}
              items={[
                {
                  label: 'Stop notifications here',
                  icon: 'delete',
                  destructive: true,
                  onSelect: () =>
                    void run(
                      () => api.DELETE('/api/me/channels/devices/{device_id}', { params: { path: { device_id: d.id } } }).then(unwrap),
                      reload,
                      'That device will stop getting notifications.',
                    ),
                },
              ]}
            />
          </div>
        </PanelRow>
      ))}
      {data.email_available && (
        <PanelRow>
          <div className="me-row">
            <Icon name="mail" />
            <label className="me-row__text" htmlFor="ch-email">
              <span className="me-row__title">Email</span>
              <span className="me-row__sub">{data.email ?? 'Not set up'}</span>
            </label>
            <span className="me-row__acts">
              {data.email ? (
                <>
                  <Switch
                    id="ch-email"
                    label="Email notifications"
                    checked={data.email_notifications}
                    onChange={(v) =>
                      void run(() => api.PUT('/api/me/channels/email/enabled', { body: { enabled: v } }).then(unwrap), reload)
                    }
                  />
                  <RowMenu
                    label="More for email"
                    items={[
                      { label: 'Change address…', icon: 'edit', onSelect: () => setEmailOpen(true) },
                      {
                        label: 'Remove email',
                        icon: 'delete',
                        destructive: true,
                        onSelect: () => void run(() => api.DELETE('/api/me/channels/email').then(unwrap), reload, 'Email removed.'),
                      },
                    ]}
                  />
                </>
              ) : (
                <Button variant="outline" onClick={() => setEmailOpen(true)}>
                  Add email
                </Button>
              )}
            </span>
          </div>
        </PanelRow>
      )}
      {data.telegram_available && (
        <PanelRow>
          <div className="me-row">
            <Icon name="send" />
            <label className="me-row__text" htmlFor="ch-telegram">
              <span className="me-row__title">Telegram</span>
              <span className="me-row__sub">
                {data.telegram_connected
                  ? data.telegram_username
                    ? `@${data.telegram_username}`
                    : 'Connected'
                  : telegramUrl
                    ? 'Waiting for you to press Start in Telegram…'
                    : 'Not connected'}
              </span>
            </label>
            <span className="me-row__acts">
              {data.telegram_connected ? (
                <>
                  <Switch
                    id="ch-telegram"
                    label="Telegram notifications"
                    checked={data.telegram_notifications}
                    onChange={(v) =>
                      void run(() => api.PUT('/api/me/channels/telegram/enabled', { body: { enabled: v } }).then(unwrap), reload)
                    }
                  />
                  <RowMenu
                    label="More for Telegram"
                    items={[
                      {
                        label: 'Disconnect Telegram',
                        icon: 'link_off',
                        destructive: true,
                        onSelect: () =>
                          void run(() => api.DELETE('/api/me/channels/telegram').then(unwrap), reload, 'Telegram disconnected.'),
                      },
                    ]}
                  />
                </>
              ) : (
                <Button
                  variant="outline"
                  onClick={() =>
                    void run(
                      async () => {
                        const { url } = await api.POST('/api/me/channels/telegram').then(unwrap)
                        setTelegramUrl(url)
                        window.open(url, '_blank', 'noopener')
                      },
                      () => undefined,
                      'Telegram is open. Press Start there to finish.',
                    )
                  }
                >
                  Connect
                </Button>
              )}
            </span>
          </div>
        </PanelRow>
      )}
      <EmailDialog open={emailOpen} onOpenChange={setEmailOpen} current={data.email} onDone={reload} />
      <TestDialog open={testOpen} onOpenChange={setTestOpen} data={data} />
    </Panel>
  )
}

function EmailDialog({
  open,
  onOpenChange,
  current,
  onDone,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  current?: string | null
  onDone: () => void
}) {
  const [email, setEmail] = useState(current ?? '')
  const [code, setCode] = useState('')
  const [sent, setSent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setSent(false)
      setCode('')
      setError(null)
      setEmail(current ?? '')
    }
  }, [open, current])

  async function step() {
    setBusy(true)
    setError(null)
    try {
      if (!sent) {
        await api.POST('/api/me/channels/email', { body: { email } }).then(unwrap)
        setSent(true)
      } else {
        await api.POST('/api/me/channels/email/confirm', { body: { code } }).then(unwrap)
        onOpenChange(false)
        onDone()
      }
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      size="sm"
      title={current ? 'Change your email' : 'Add your email'}
      description="We send a 6-digit code to check the address is yours."
      primaryLabel={sent ? 'Confirm' : 'Send code'}
      busy={busy}
      busyLabel={sent ? 'Checking…' : 'Sending…'}
      onPrimary={() => void step()}
    >
      <div className="me-form">
        <label className="field">
          <span className="field__label">Email address</span>
          <input className="input" type="email" autoComplete="email" value={email} disabled={sent} onChange={(e) => setEmail(e.target.value)} />
        </label>
        {sent && (
          <label className="field">
            <span className="field__label">Code from the email</span>
            <input className="input" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} />
            <span className="field__hint">It expires in 15 minutes.</span>
          </label>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </FormDialog>
  )
}

function TestDialog({ open, onOpenChange, data }: { open: boolean; onOpenChange: (o: boolean) => void; data: Channels }) {
  const options: { key: string; label: string; ready: boolean; on: boolean }[] = []
  if (data.discord) options.push({ key: 'discord', label: 'Discord DMs', ready: true, on: data.dm_notifications })
  if (data.push_configured)
    options.push({ key: 'devices', label: data.devices.length ? 'Your devices' : 'Your devices (none turned on)', ready: data.devices.length > 0, on: data.devices.length > 0 })
  if (data.email_available) options.push({ key: 'email', label: data.email ? 'Email' : 'Email (not set up)', ready: !!data.email, on: !!data.email && data.email_notifications })
  if (data.telegram_available)
    options.push({ key: 'telegram', label: data.telegram_connected ? 'Telegram' : 'Telegram (not connected)', ready: data.telegram_connected, on: data.telegram_connected && data.telegram_notifications })

  const [chosen, setChosen] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setChosen(new Set(options.filter((o) => o.on).map((o) => o.key)))
      setResult(null)
      setError(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  async function send() {
    setBusy(true)
    setError(null)
    try {
      const { results } = await api.POST('/api/me/channels/test', { body: { channels: [...chosen] } }).then(unwrap)
      const label = (k: string) => options.find((o) => o.key === k)?.label ?? k
      const ok = Object.entries(results).filter(([, v]) => v).map(([k]) => label(k))
      const bad = Object.entries(results).filter(([, v]) => !v).map(([k]) => label(k))
      setResult([ok.length ? `Sent to ${ok.join(', ')}.` : '', bad.length ? `Couldn't reach ${bad.join(', ')}.` : ''].filter(Boolean).join(' '))
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      size="sm"
      title="Send a test"
      description="Check it works: we send a test to the places you pick. You can send 3 every 10 minutes."
      primaryLabel="Send test"
      busy={busy}
      busyLabel="Sending…"
      onPrimary={() => void send()}
    >
      <div className="me-form">
        <fieldset className="me-checks" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="visually-hidden">Where to send it</legend>
          {options.map((o) => (
            <label key={o.key}>
              <input
                type="checkbox"
                disabled={!o.ready}
                checked={chosen.has(o.key)}
                onChange={(e) => {
                  const next = new Set(chosen)
                  if (e.target.checked) next.add(o.key)
                  else next.delete(o.key)
                  setChosen(next)
                }}
              />
              {o.label}
            </label>
          ))}
        </fieldset>
        {result && (
          <p className="me-saved" role="status">
            {result}
          </p>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </FormDialog>
  )
}

function HearPanel({ mine, run, reload }: { mine: Mine; run: Run; reload: () => void }) {
  const [open, setOpen] = useState(false)
  const sections = [...new Set(mine.messages.map((m) => m.section))]
  return (
    <Panel title="How much you hear">
      <PanelRow>
        <div className="me-form" style={{ width: '100%' }}>
          <p className="me-note">
            Start from a preset, then change single messages under Customize. Your community decides what gets sent; you can make
            anything quieter.
          </p>
          <div className="me-presets" role="radiogroup" aria-label="How much you hear">
            {PRESETS.map((p) => (
              <span key={p.value}>
                <input
                  type="radio"
                  id={`preset-${p.value}`}
                  name="preset"
                  checked={mine.preset === p.value}
                  onChange={() =>
                    void run(() => api.PUT('/api/notifications/messages/preset', { body: { preset: p.value } }).then(unwrap), reload)
                  }
                />
                <label htmlFor={`preset-${p.value}`}>
                  <strong>{p.title}</strong>
                  {p.blurb}
                </label>
              </span>
            ))}
          </div>
        </div>
      </PanelRow>
      <PanelRow>
        <div className="me-row">
          <span className="me-row__text">
            <span className="me-row__title">Customize</span>
            <span className="me-row__sub">
              {mine.changed
                ? `${mine.changed} message${mine.changed === 1 ? '' : 's'} changed from your preset`
                : 'Choose Off, Silent or Ping for any single message'}
            </span>
          </span>
          <span className="me-row__acts">
            {mine.changed > 0 && (
              <Button
                variant="ghost"
                icon="restart_alt"
                onClick={() => void run(() => api.POST('/api/notifications/messages/reset').then(unwrap), reload, 'Back to your preset.')}
              >
                Reset
              </Button>
            )}
            <Button variant="outline" aria-expanded={open} onClick={() => setOpen(!open)}>
              {open ? 'Hide' : 'Show'}
            </Button>
          </span>
        </div>
      </PanelRow>
      {open &&
        sections.map((section) => (
          <div key={section} role="group" aria-labelledby={`sec-${section}`}>
            <h3 className="me-group" id={`sec-${section}`}>
              {section}
            </h3>
            {mine.messages
              .filter((m) => m.section === section)
              .map((m) => {
                const id = `${m.notice}-${m.recipient}`
                return (
                  <PanelRow key={id}>
                    <div className="me-row">
                      <span className="me-row__text">
                        <span className="me-row__title" id={`t-${id}`}>
                          {m.title}
                        </span>
                        <span className="me-row__sub">{m.when}</span>
                      </span>
                      <div className="segmented" role="radiogroup" aria-labelledby={`t-${id}`}>
                        {MODES.map((mode) => {
                          const louder = (ORDER[mode.value] ?? 0) > (ORDER[m.community] ?? 2)
                          return (
                            <span key={mode.value}>
                              <input
                                type="radio"
                                id={`${id}-${mode.value}`}
                                name={id}
                                disabled={louder}
                                checked={m.mode === mode.value}
                                onChange={() =>
                                  void run(
                                    () =>
                                      api
                                        .PUT('/api/notifications/messages/{notice}/{recipient}', {
                                          params: { path: { notice: m.notice, recipient: m.recipient } },
                                          body: { mode: mode.value === m.preset ? null : mode.value },
                                        })
                                        .then(unwrap),
                                    reload,
                                  )
                                }
                              />
                              <label htmlFor={`${id}-${mode.value}`}>{mode.label}</label>
                            </span>
                          )
                        })}
                      </div>
                    </div>
                  </PanelRow>
                )
              })}
          </div>
        ))}
    </Panel>
  )
}
