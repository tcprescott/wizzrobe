import { useState } from 'react'
import { api, unwrap, type Schemas } from '../api/client'
import { Button, FormDialog } from '../components'
import { DialogError, message } from './parts'

type Account = Schemas['PlatformUserResponse']

const ALPHABET = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'

function generate(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(20))
  return Array.from(bytes, (b) => ALPHABET[b % ALPHABET.length]).join('')
}

function PasswordField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="field">
      <label className="field__label" htmlFor="pf-password">
        Password
      </label>
      <input id="pf-password" className="input pf-mono" value={value} autoComplete="new-password" onChange={(e) => onChange(e.target.value)} />
      <span className="field__hint">At least 12 characters. Send it to them somewhere private; it isn't shown again.</span>
      <div className="pf-toolbar">
        <Button variant="outline" size="sm" icon="casino" onClick={() => onChange(generate())}>
          Generate
        </Button>
        <Button
          variant="outline"
          size="sm"
          icon="content_copy"
          disabled={!value}
          onClick={async () => {
            await navigator.clipboard.writeText(value)
            setCopied(true)
          }}
        >
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>
    </div>
  )
}

/** Give an account a sign-in name and password, or change them. */
export function SetPasswordDialog({ account, onClose, onSaved }: { account: Account; onClose: () => void; onSaved: (text: string) => void }) {
  const [login, setLogin] = useState(account.login_name ?? account.username)
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const verb = account.login_name ? 'Change' : 'Set'
  const who = account.display_name || account.username
  return (
    <FormDialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={`${verb} password for ${who}`}
      description="For someone without Discord, or a shared venue laptop. They sign in at the unpublished password sign-in page."
      primaryLabel={`${verb} password`}
      busy={busy}
      busyLabel="Saving…"
      onPrimary={async () => {
        setBusy(true)
        setError(null)
        try {
          await api
            .PUT('/api/platform/users/{user_id}/password', { params: { path: { user_id: account.id } }, body: { login_name: login, password } })
            .then(unwrap)
          onSaved(`${who} can sign in as ${login.toLowerCase()}.`)
        } catch (err) {
          setError(message(err))
        } finally {
          setBusy(false)
        }
      }}
    >
      <div className="pf-form">
        <DialogError error={error} />
        <label className="field">
          <span className="field__label">Sign-in name</span>
          <input className="input" value={login} autoComplete="off" onChange={(e) => setLogin(e.target.value)} />
        </label>
        <PasswordField value={password} onChange={setPassword} />
      </div>
    </FormDialog>
  )
}

/** A new account with no Discord that signs in only by password. */
export function NewAccountDialog({ onClose, onSaved }: { onClose: () => void; onSaved: (text: string) => void }) {
  const [name, setName] = useState('')
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return (
    <FormDialog
      open
      onOpenChange={(open) => !open && onClose()}
      title="New password account"
      description="An account with no Discord link. It joins no community until a community's staff add it."
      primaryLabel="Create account"
      busy={busy}
      busyLabel="Creating…"
      onPrimary={async () => {
        setBusy(true)
        setError(null)
        try {
          await api.POST('/api/platform/users', { body: { display_name: name, login_name: login, password } }).then(unwrap)
          onSaved(`${name} created. They sign in as ${login.toLowerCase()}.`)
        } catch (err) {
          setError(message(err))
        } finally {
          setBusy(false)
        }
      }}
    >
      <div className="pf-form">
        <DialogError error={error} />
        <label className="field">
          <span className="field__label">Display name</span>
          <input className="input" value={name} maxLength={150} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">Sign-in name</span>
          <input className="input" value={login} autoComplete="off" onChange={(e) => setLogin(e.target.value)} />
          <span className="field__hint">3 to 64 letters, digits, dots, dashes and underscores.</span>
        </label>
        <PasswordField value={password} onChange={setPassword} />
      </div>
    </FormDialog>
  )
}
