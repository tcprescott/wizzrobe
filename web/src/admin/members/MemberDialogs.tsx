import { useEffect, useState } from 'react'
import { api, unwrap, type Schemas } from '../../api/client'
import { Banner, Dialog, FormDialog, Spinner } from '../../components'
import { formatDay } from '../../lib/time'
import { Avatar, useAction } from '../peopleKit'

interface OpenProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

function FormError({ error }: { error: string | null }) {
  if (!error) return null
  return (
    <p className="form-error" role="alert">
      {error}
    </p>
  )
}

export function MessageDialog({ open, onOpenChange, userId, name, onSent }: OpenProps & { userId: number; name: string; onSent: () => void }) {
  const [text, setText] = useState('')
  const { busy, error, run, setError } = useAction()
  useEffect(() => {
    if (open) {
      setText('')
      setError(null)
    }
  }, [open, setError])
  async function send() {
    const ok = await run('send', () =>
      api.POST('/api/admin/members/{user_id}/message', { params: { path: { user_id: userId } }, body: { message: text } }).then(unwrap),
    )
    if (ok) {
      onOpenChange(false)
      onSent()
    }
  }
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Message ${name}`}
      description="Sent as a Discord DM from the Wizzrobe bot, and to their phone if they get notifications there. It always pings."
      primaryLabel="Send"
      busy={busy === 'send'}
      busyLabel="Sending…"
      onPrimary={() => void send()}
    >
      <label className="field">
        <span className="field__label">Message</span>
        <textarea className="input" rows={5} maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} />
        <span className="field__hint">{text.length} / 2,000</span>
      </label>
      <FormError error={error} />
    </FormDialog>
  )
}

export function ProfileDialog({
  open,
  onOpenChange,
  userId,
  displayName,
  pronouns,
  onSaved,
}: OpenProps & { userId: number; displayName: string; pronouns: string; onSaved: () => void }) {
  const [name, setName] = useState(displayName)
  const [pro, setPro] = useState(pronouns)
  const { busy, error, run, setError } = useAction()
  useEffect(() => {
    if (open) {
      setName(displayName)
      setPro(pronouns)
      setError(null)
    }
  }, [open, displayName, pronouns, setError])
  async function save() {
    const ok = await run('save', () =>
      api
        .PATCH('/api/users/{user_id}', {
          params: { path: { user_id: userId } },
          body: { display_name: name.trim() || null, pronouns: pro.trim() || null },
        })
        .then(unwrap),
    )
    if (ok) {
      onOpenChange(false)
      onSaved()
    }
  }
  return (
    <FormDialog open={open} onOpenChange={onOpenChange} title="Edit profile" size="sm" primaryLabel="Save" busy={busy === 'save'} onPrimary={() => void save()}>
      <label className="field">
        <span className="field__label">Display name</span>
        <input className="input" value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
        <span className="field__hint">Leave empty to use their username.</span>
      </label>
      <label className="field">
        <span className="field__label">Pronouns</span>
        <input className="input" value={pro} maxLength={50} onChange={(e) => setPro(e.target.value)} />
      </label>
      <FormError error={error} />
    </FormDialog>
  )
}

/** Opening it is the audited look, so nothing loads until the dialog opens. */
export function EmergencyContactDialog({ open, onOpenChange, userId, name, zone }: OpenProps & { userId: number; name: string; zone: string }) {
  const [contact, setContact] = useState<Schemas['EmergencyContactViewResponse'] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!open) return
    let live = true
    setContact(null)
    setError(null)
    api
      .POST('/api/admin/members/{user_id}/emergency-contact', { params: { path: { user_id: userId } } })
      .then(unwrap)
      .then((c) => live && setContact(c))
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)))
    return () => {
      live = false
    }
  }, [open, userId])
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={`${name}'s emergency contact`}
      size="sm"
      description="Opening this is logged in the audit log."
    >
      {error ? (
        <Banner tone="error">{error}</Banner>
      ) : !contact ? (
        <Spinner label="Loading emergency contact" />
      ) : !contact.found ? (
        <p>They haven't given an emergency contact.</p>
      ) : (
        <dl className="contact-card">
          <dt>Name</dt>
          <dd>{contact.name}</dd>
          {contact.relationship && (
            <>
              <dt>Relationship</dt>
              <dd>{contact.relationship}</dd>
            </>
          )}
          <dt>Phone</dt>
          <dd>
            <a href={`tel:${contact.phone ?? ''}`}>{contact.phone}</a>
          </dd>
          {contact.updated_at && (
            <>
              <dt>Last updated</dt>
              <dd>{formatDay(contact.updated_at, zone)}</dd>
            </>
          )}
        </dl>
      )}
    </Dialog>
  )
}

export function AddMemberDialog({ open, onOpenChange, onAdded }: OpenProps & { onAdded: (userId: number) => void }) {
  const [accounts, setAccounts] = useState<Schemas['AddableAccountResponse'][] | null>(null)
  const [search, setSearch] = useState('')
  const [picked, setPicked] = useState<number | null>(null)
  const { busy, error, run, setError } = useAction()
  useEffect(() => {
    if (!open) return
    setSearch('')
    setPicked(null)
    setError(null)
    api
      .GET('/api/admin/members/addable')
      .then(unwrap)
      .then(setAccounts)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [open, setError])
  const q = search.trim().toLowerCase()
  const shown = (accounts ?? []).filter((a) => !q || a.preferred_name.toLowerCase().includes(q) || a.username.toLowerCase().includes(q)).slice(0, 50)
  async function add() {
    if (picked === null) return
    const ok = await run('add', () => api.POST('/api/users/{user_id}/membership', { params: { path: { user_id: picked } } }).then(unwrap))
    if (ok) {
      onOpenChange(false)
      onAdded(picked)
    }
  }
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Add a member"
      description="Anyone with a Wizzrobe account who isn't in this community yet. They aren't messaged."
      primaryLabel="Add"
      busy={busy === 'add'}
      onPrimary={() => void add()}
    >
      <input
        className="input admin-search"
        type="search"
        placeholder="Search names"
        aria-label="Search accounts"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      {!accounts && !error ? (
        <Spinner label="Loading accounts" />
      ) : (
        <ul className="pick-list" aria-label="Accounts">
          {shown.map((a) => (
            <li key={a.id}>
              <label className="pick-list__row">
                <input type="radio" name="add-member" checked={picked === a.id} onChange={() => setPicked(a.id)} />
                <Avatar name={a.preferred_name} url={a.avatar_url} />
                <span className="admin-name">
                  <span>{a.preferred_name}</span>
                  <span className="admin-name__sub">@{a.username}</span>
                </span>
              </label>
            </li>
          ))}
          {accounts && shown.length === 0 && <li className="admin-muted">No one matches.</li>}
        </ul>
      )}
      <FormError error={error} />
    </FormDialog>
  )
}

export function DiscordImportDialog({ open, onOpenChange, onDone }: OpenProps & { onDone: () => void }) {
  const [preview, setPreview] = useState<Schemas['DiscordImportPreviewResponse'] | null>(null)
  const [result, setResult] = useState<Schemas['DiscordImportResultResponse'] | null>(null)
  const { busy, error, run, setError } = useAction()
  useEffect(() => {
    if (!open) return
    setPreview(null)
    setResult(null)
    setError(null)
    api
      .GET('/api/admin/members/discord-import')
      .then(unwrap)
      .then(setPreview)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [open, setError])
  async function go() {
    await run('import', async () => {
      setResult(await api.POST('/api/admin/members/discord-import').then(unwrap))
      onDone()
    })
  }
  if (result) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange} title="Import done" size="sm">
        <p>
          Added {result.members_added} member{result.members_added === 1 ? '' : 's'}
          {result.accounts_created ? `, ${result.accounts_created} with new accounts` : ''}. {result.already_members} were already here.
        </p>
      </Dialog>
    )
  }
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Import from Discord"
      size="sm"
      primaryLabel={preview ? `Add ${preview.new_members}` : 'Import'}
      busy={busy === 'import'}
      busyLabel="Importing…"
      onPrimary={() => void go()}
    >
      {!preview && !error && <Spinner label="Reading the Discord server" />}
      {preview && (
        <>
          <p>
            {preview.server_name} has {preview.people} people. {preview.new_members} aren't members here yet
            {preview.new_accounts ? `, and ${preview.new_accounts} of them need a Wizzrobe account` : ''}.
          </p>
          <p className="admin-muted">
            No one is messaged. Skipped: {preview.bots} bots, {preview.removed} removed from this community, {preview.already_members} already members. Safe to run again.
          </p>
        </>
      )}
      <FormError error={error} />
    </FormDialog>
  )
}
