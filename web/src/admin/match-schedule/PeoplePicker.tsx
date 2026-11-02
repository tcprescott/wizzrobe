import { useId, useState } from 'react'
import { Icon } from '../../components'

interface Person {
  id: number
  name: string
}

/** Picked people as removable chips, and a search box that offers the rest. */
export function PeoplePicker({
  label,
  people,
  value,
  onChange,
  max,
  hint,
}: {
  label: string
  people: Person[]
  value: number[]
  onChange: (ids: number[]) => void
  max?: number | null
  hint?: string
}) {
  const id = useId()
  const [q, setQ] = useState('')
  const byId = new Map(people.map((p) => [p.id, p]))
  const needle = q.trim().toLowerCase()
  const offers = needle
    ? people.filter((p) => !value.includes(p.id) && p.name.toLowerCase().includes(needle)).slice(0, 6)
    : []
  const full = max != null && max > 0 && value.length >= max
  return (
    <div className="field ms-people">
      <label className="field__label" htmlFor={id}>
        {label}
      </label>
      {value.length > 0 && (
        <ul className="ms-people__picked" aria-label={`${label} picked`}>
          {value.map((pid) => {
            const name = byId.get(pid)?.name ?? `Member ${pid}`
            return (
              <li key={pid} className="ms-people__chip">
                {name}
                <button
                  type="button"
                  className="ms-people__remove"
                  aria-label={`Remove ${name}`}
                  onClick={() => onChange(value.filter((v) => v !== pid))}
                >
                  <Icon name="close" size={16} />
                </button>
              </li>
            )
          })}
        </ul>
      )}
      {!full && (
        <input
          id={id}
          className="input"
          type="search"
          placeholder="Type a name"
          autoComplete="off"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      )}
      {offers.length > 0 && (
        <ul className="ms-people__offers" aria-label={`Add to ${label.toLowerCase()}`}>
          {offers.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                className="ms-people__offer"
                onClick={() => {
                  onChange([...value, p.id])
                  setQ('')
                }}
              >
                <Icon name="add" size={18} />
                {p.name}
              </button>
            </li>
          ))}
        </ul>
      )}
      {needle && offers.length === 0 && <span className="field__hint">Nobody here by that name.</span>}
      {hint && <span className="field__hint">{hint}</span>}
    </div>
  )
}
