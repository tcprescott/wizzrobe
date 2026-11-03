import { useId } from 'react'
import { Banner, Button } from '../../components'
import { effectiveZone, wallClock, zonedInstant } from '../../lib/time'

/** A date and a time input for one instant, read and written on `zone`'s clock. Empty date clears it. */
export function InstantField({
  label,
  value,
  zone,
  onChange,
  hint,
}: {
  label: string
  value: string | null
  zone: string | null
  onChange: (iso: string | null) => void
  hint?: string
}) {
  const z = effectiveZone(zone)
  const wall = value ? wallClock(value, z) : { date: '', time: '' }
  const set = (date: string, time: string) => onChange(date ? zonedInstant(date, time || '00:00', z).toISOString() : null)
  return (
    <fieldset className="field t-instant">
      <legend className="field__label">{label}</legend>
      <div className="t-instant__inputs">
        <input
          className="input"
          type="date"
          aria-label={`${label}, date`}
          value={wall.date}
          onChange={(e) => set(e.target.value, wall.time)}
        />
        <input
          className="input"
          type="time"
          aria-label={`${label}, time`}
          value={wall.time}
          disabled={!wall.date}
          onChange={(e) => set(wall.date, e.target.value)}
        />
      </div>
      {hint && <span className="field__hint">{hint}</span>}
    </fieldset>
  )
}

export function NumberField({
  label,
  value,
  onChange,
  min = 0,
}: {
  label: string
  value: number | null
  onChange: (n: number | null) => void
  min?: number
}) {
  return (
    <label className="field">
      <span className="field__label">{label}</span>
      <input
        className="input"
        type="number"
        inputMode="numeric"
        min={min}
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
      />
    </label>
  )
}

/** A whole number of minutes; blank means "use the default". */
export function MinutesField({
  label,
  hint,
  value,
  onChange,
  min = 1,
}: {
  label: string
  hint: string
  value: number | null
  onChange: (value: number | null) => void
  min?: number
}) {
  const id = useId()
  return (
    <div className="field settings-number">
      <label className="field__label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        className="input"
        type="number"
        inputMode="numeric"
        min={min}
        step={1}
        value={value ?? ''}
        aria-describedby={`${id}-hint`}
        onChange={(e) => onChange(e.target.value === '' ? null : Math.trunc(Number(e.target.value)))}
      />
      <span className="field__hint" id={`${id}-hint`}>
        {hint}
      </span>
    </div>
  )
}

export function LoadError({ error, onRetry }: { error: Error; onRetry: () => void }) {
  return (
    <Banner
      tone="error"
      action={
        <Button variant="outline" onClick={onRetry}>
          Try again
        </Button>
      }
    >
      {error.message}
    </Banner>
  )
}

/** A group of checkboxes in a bordered list, for picking several of a short set. */
export function CheckList<V extends string>({
  legend,
  items,
  value,
  onChange,
}: {
  legend: string
  items: { value: V; label: string; hint?: string; color?: number }[]
  value: V[]
  onChange: (value: V[]) => void
}) {
  return (
    <fieldset className="settings-checks">
      <legend className="field__label">{legend}</legend>
      <div className="settings-checks__box">
        {items.map((item) => (
          <label key={item.value} className="t-check">
            <input
              type="checkbox"
              checked={value.includes(item.value)}
              onChange={(e) => onChange(e.target.checked ? [...value, item.value] : value.filter((v) => v !== item.value))}
            />
            {item.color !== undefined && (
              <span
                className="settings-checks__dot"
                aria-hidden="true"
                style={{ background: item.color ? `#${item.color.toString(16).padStart(6, '0')}` : undefined }}
              />
            )}
            <span>
              {item.label}
              {item.hint && <span className="settings-row__sub"> · {item.hint}</span>}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}
