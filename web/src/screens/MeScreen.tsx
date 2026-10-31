import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { Banner, Button, FormDialog, Icon, PageHeader, Panel, PanelRow, Spinner, type IconName } from '../components'
import { useTheme, type ThemeChoice } from '../theme/ThemeProvider'
import { useMe } from './useMe'
import './screens.css'

const THEMES: { value: ThemeChoice; label: string; icon: IconName }[] = [
  { value: 'system', label: 'System', icon: 'brightness_auto' },
  { value: 'light', label: 'Light', icon: 'light_mode' },
  { value: 'dark', label: 'Dark', icon: 'dark_mode' },
]

export function MeScreen() {
  const { me, error, loading } = useMe()
  const { choice, setChoice } = useTheme()
  const { signOut } = useAuth()
  const navigate = useNavigate()
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)

  const doSignOut = async () => {
    setBusy(true)
    await signOut()
    navigate('/sign-in', { replace: true })
  }

  return (
    <>
      <PageHeader title="Me" lede="Your profile and how the app looks." />
      <Panel title="Profile">
        {loading && !me && <Spinner label="Loading your profile…" />}
        {error && <Banner tone="error">{error.message}</Banner>}
        {me && (
          <PanelRow>
            <Icon name="person" />
            <span>
              <strong>{me.display_name || me.username}</strong>
              <span className="list-link__sub">@{me.username}</span>
            </span>
          </PanelRow>
        )}
      </Panel>
      <Panel title="Appearance">
        <PanelRow>
          <div className="field-row">
            <span className="field-row__label" id="theme-label">
              Theme
            </span>
            <div className="segmented" role="radiogroup" aria-labelledby="theme-label">
              {THEMES.map((t) => (
                <span key={t.value}>
                  <input
                    type="radio"
                    id={`theme-${t.value}`}
                    name="theme"
                    value={t.value}
                    checked={choice === t.value}
                    onChange={() => setChoice(t.value)}
                  />
                  <label htmlFor={`theme-${t.value}`}>
                    <Icon name={t.icon} size={18} />
                    {t.label}
                  </label>
                </span>
              ))}
            </div>
          </div>
        </PanelRow>
      </Panel>
      <Panel title="Account">
        <PanelRow>
          <div className="field-row">
            <span>Sign out of Wizzrobe on this device.</span>
            <Button variant="outline" icon="logout" onClick={() => setConfirming(true)}>
              Sign out
            </Button>
          </div>
        </PanelRow>
      </Panel>
      <FormDialog
        open={confirming}
        onOpenChange={setConfirming}
        size="sm"
        title="Sign out?"
        description="You'll need to sign in again to see your matches and shifts on this device."
        primaryLabel="Sign out"
        busy={busy}
        busyLabel="Signing out…"
        onPrimary={doSignOut}
      >
        {null}
      </FormDialog>
    </>
  )
}
