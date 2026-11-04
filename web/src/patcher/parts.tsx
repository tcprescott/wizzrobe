import { useState } from 'react'
import { Button, Icon } from '../components'

/** Copies the page's own address, for passing the seed or patch on. */
export function CopyLinkButton() {
  const [copied, setCopied] = useState(false)
  return (
    <Button
      variant="ghost"
      icon={copied ? 'check' : 'link'}
      onClick={() => void navigator.clipboard?.writeText(window.location.href).then(() => setCopied(true))}
    >
      {copied ? 'Link copied' : 'Copy link'}
    </Button>
  )
}

/** The ROM this device remembers, with a way to pick another. */
export function RomLine({ name, detail, onChange, busy }: { name: string; detail: string; onChange: () => void; busy?: boolean }) {
  return (
    <div className="patcher-rom">
      <Icon name="videogame_asset" />
      <div className="patcher-rom__text">
        <p className="patcher-summary__main">{name}</p>
        <p className="patcher-hint">{detail}</p>
      </div>
      <Button variant="outline" size="sm" busy={busy} busyLabel="Checking…" onClick={onChange}>
        Change
      </Button>
    </div>
  )
}
