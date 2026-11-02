import { createContext, useCallback, useContext, useEffect, useId, useMemo, useState, type ReactNode } from 'react'
import { useBlocker } from 'react-router-dom'
import { Button, FormDialog, Icon, type IconName } from '../../components'
import './settings.css'

type DirtyRegistry = (id: string, dirty: boolean) => void

const DirtyContext = createContext<DirtyRegistry | null>(null)

/**
 * Wraps a screen of settings cards. While any card holds unsaved changes,
 * leaving (another screen, or closing the tab) asks first.
 */
export function UnsavedChangesGuard({ children }: { children: ReactNode }) {
  const [dirty, setDirty] = useState<ReadonlySet<string>>(new Set())
  const register = useCallback<DirtyRegistry>((id, isDirty) => {
    setDirty((prev) => {
      if (prev.has(id) === isDirty) return prev
      const next = new Set(prev)
      if (isDirty) next.add(id)
      else next.delete(id)
      return next
    })
  }, [])
  const blocking = dirty.size > 0
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) => blocking && currentLocation.pathname !== nextLocation.pathname,
  )

  useEffect(() => {
    if (!blocking) return
    const warn = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [blocking])

  return (
    <DirtyContext.Provider value={register}>
      {children}
      <FormDialog
        open={blocker.state === 'blocked'}
        onOpenChange={(open) => {
          if (!open) blocker.reset?.()
        }}
        title="Leave without saving?"
        description={`${dirty.size === 1 ? 'A card here has' : `${dirty.size} cards here have`} changes that aren't saved yet.`}
        cancelLabel="Keep editing"
        primaryLabel="Leave"
        destructive
        onPrimary={() => blocker.proceed?.()}
        size="sm"
      >
        <p className="settings-card__note">Leaving throws those changes away.</p>
      </FormDialog>
    </DirtyContext.Provider>
  )
}

/** A form's value: the saved one until someone edits, then their draft. */
export function useDraft<T>(saved: T | undefined) {
  const [draft, setDraft] = useState<T | null>(null)
  const value = (draft ?? saved) as T | undefined
  const dirty = draft !== null && JSON.stringify(draft) !== JSON.stringify(saved)
  return useMemo(
    () => ({
      value,
      dirty,
      set: (next: T) => setDraft(next),
      patch: (part: Partial<T>) => setDraft({ ...(value as T), ...part }),
      discard: () => setDraft(null),
    }),
    [value, dirty],
  )
}

interface SettingsCardProps {
  title: string
  icon?: IconName
  /** One line under the title: what the card decides. */
  subtitle?: string
  dirty: boolean
  /** Saves the card; resolve when done, throw an Error whose message the card shows. */
  onSave: () => Promise<void>
  onDiscard: () => void
  /** Read-only viewers see the values but no footer. */
  readOnly?: boolean
  children: ReactNode
}

/**
 * One card of a settings pane that saves on its own: Save and Discard appear
 * once something in it changes, and a pane's other cards aren't touched.
 */
export function SettingsCard({ title, icon, subtitle, dirty, onSave, onDiscard, readOnly, children }: SettingsCardProps) {
  const id = useId()
  const register = useContext(DirtyContext)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    register?.(id, dirty)
    if (dirty) setSaved(false)
  }, [register, id, dirty])
  useEffect(() => () => register?.(id, false), [register, id])

  async function save() {
    setBusy(true)
    setError(null)
    try {
      await onSave()
      setSaved(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const headingId = `${id}-title`
  return (
    <section className="panel settings-card" aria-labelledby={headingId}>
      <header className="settings-card__head">
        {icon && <Icon name={icon} size={20} />}
        <div className="settings-card__heading">
          <h2 className="settings-card__title" id={headingId}>
            {title}
          </h2>
          {subtitle && <p className="settings-card__sub">{subtitle}</p>}
        </div>
      </header>
      <div className="settings-card__body">{children}</div>
      {!readOnly && (dirty || saved || error) && (
        <footer className="settings-card__foot">
          {error ? (
            <span className="form-error" role="alert">
              {error}
            </span>
          ) : (
            <span className={dirty ? 'settings-card__state' : 'settings-card__state settings-card__state--saved'} role="status">
              {dirty ? 'Unsaved changes' : 'Saved'}
            </span>
          )}
          {dirty && (
            <>
              <Button
                variant="ghost"
                onClick={() => {
                  setError(null)
                  onDiscard()
                }}
                disabled={busy}
              >
                Discard
              </Button>
              <Button variant="primary" busy={busy} busyLabel="Saving…" onClick={() => void save()}>
                Save
              </Button>
            </>
          )}
        </footer>
      )}
    </section>
  )
}

/** A labelled switch row: title, a line saying what on means, the switch at the end. */
export function SwitchRow({
  label,
  hint,
  checked,
  onChange,
  disabled,
}: {
  label: string
  hint?: string
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
}) {
  const id = useId()
  return (
    <label className="settings-row" htmlFor={id}>
      <span className="settings-row__text">
        <span className="settings-row__title">{label}</span>
        {hint && <span className="settings-row__sub">{hint}</span>}
      </span>
      <input
        id={id}
        type="checkbox"
        role="switch"
        className="switch"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
    </label>
  )
}

/** A radio group, one option per row, each with a line under it. */
export function RadioRows<V extends string>({
  label,
  name,
  value,
  options,
  onChange,
  disabled,
}: {
  label: string
  name: string
  value: V
  options: { value: V; label: string; hint?: string }[]
  onChange: (value: V) => void
  disabled?: boolean
}) {
  return (
    <fieldset className="settings-radios">
      <legend className="visually-hidden">{label}</legend>
      {options.map((o) => (
        <label key={o.value} className="settings-row settings-row--radio">
          <input
            type="radio"
            name={name}
            value={o.value}
            checked={value === o.value}
            disabled={disabled}
            onChange={() => onChange(o.value)}
          />
          <span className="settings-row__text">
            <span className="settings-row__title">{o.label}</span>
            {o.hint && <span className="settings-row__sub">{o.hint}</span>}
          </span>
        </label>
      ))}
    </fieldset>
  )
}

/**
 * A settings card whose rows act straight away (a list with a ⋮ per row), so
 * it has no Save footer. `action` is the card's one button, in its header.
 */
export function ListCard({
  title,
  icon,
  subtitle,
  action,
  children,
}: {
  title: string
  icon?: IconName
  subtitle?: string
  action?: ReactNode
  children: ReactNode
}) {
  const id = useId()
  return (
    <section className="panel settings-card" aria-labelledby={`${id}-title`}>
      <header className="settings-card__head">
        {icon && <Icon name={icon} size={20} />}
        <div className="settings-card__heading">
          <h2 className="settings-card__title" id={`${id}-title`}>
            {title}
          </h2>
          {subtitle && <p className="settings-card__sub">{subtitle}</p>}
        </div>
        {action && <div className="settings-card__action">{action}</div>}
      </header>
      {children}
    </section>
  )
}
