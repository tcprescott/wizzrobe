import { useEffect, useState } from 'react'
import { Icon } from '../../../components'

/** A search box that reports what was typed once typing pauses. */
export function SearchBox({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  const [text, setText] = useState(value)
  useEffect(() => {
    if (text === value) return
    const t = setTimeout(() => onChange(text.trim()), 350)
    return () => clearTimeout(t)
  }, [text, value, onChange])
  return (
    <label className="report-search">
      <Icon name="search" size={20} />
      <span className="visually-hidden">{label}</span>
      <input type="search" placeholder="Search" value={text} onChange={(e) => setText(e.target.value)} />
    </label>
  )
}
