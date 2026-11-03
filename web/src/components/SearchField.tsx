import { Icon } from './Icon'

/** A list's search box: magnifier, the label as placeholder (and for screen readers), clear button from the browser. */
export function SearchField({ value, onChange, label }: { value: string; onChange: (value: string) => void; label: string }) {
  return (
    <label className="search-field">
      <Icon name="search" size={20} />
      <span className="visually-hidden">{label}</span>
      <input
        className="search-field__input"
        type="search"
        value={value}
        placeholder={label}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  )
}
