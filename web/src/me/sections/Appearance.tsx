import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../../auth/AuthProvider'
import { Button, FormDialog, Icon, Panel, PanelRow, type IconName } from '../../components'
import { useTheme, type ThemeChoice } from '../../theme/ThemeProvider'
import '../../screens/screens.css'

const THEMES: { value: ThemeChoice; label: string; icon: IconName }[] = [
  { value: 'system', label: 'System', icon: 'brightness_auto' },
  { value: 'light', label: 'Light', icon: 'light_mode' },
  { value: 'dark', label: 'Dark', icon: 'dark_mode' },
]

/** How the app looks on this device, and signing out of it. */
export function Appearance() {
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
    <div className="me-stack">
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
    </div>
  )
}
