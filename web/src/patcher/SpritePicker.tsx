import { useEffect, useMemo, useState } from 'react'
import { api, unwrap, type Schemas } from '../api/client'
import { Dialog, EmptyState, Icon, SearchField, Spinner } from '../components'
import type { SpriteChoice } from './door'

type Sprite = Schemas['SpriteResponse']

const LINK: SpriteChoice = { name: 'Link', default: true }
const RANDOM: SpriteChoice = { name: 'Random sprite', random: true }

function same(a: SpriteChoice | null, b: SpriteChoice): boolean {
  if (!a || a.default) return Boolean(b.default)
  if (a.random) return Boolean(b.random)
  return !b.default && !b.random && a.name === b.name
}

/** The alttpr.com sprite list, searchable, with Link and Random first. */
export function SpritePicker({
  open,
  onOpenChange,
  current,
  onPick,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  current: SpriteChoice | null
  onPick: (sprite: SpriteChoice | null) => void
}) {
  const [sprites, setSprites] = useState<Sprite[] | null>(null)
  const [query, setQuery] = useState('')

  useEffect(() => {
    if (!open || sprites) return
    api
      .GET('/api/door-seeds/sprites')
      .then(unwrap)
      .then(setSprites)
      .catch(() => setSprites([]))
  }, [open, sprites])

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = (sprites ?? []).filter((s) => s.file)
    return q ? list.filter((s) => s.name.toLowerCase().includes(q) || (s.author ?? '').toLowerCase().includes(q)) : list
  }, [sprites, query])

  const choice = (c: SpriteChoice, label: string, sub: string, preview?: string | null) => (
    <li key={`${c.default ? 'd' : c.random ? 'r' : ''}${c.name}`}>
      <button
        type="button"
        className={`sprite-choice${same(current, c) ? ' is-picked' : ''}`}
        aria-pressed={same(current, c)}
        onClick={() => onPick(c.default ? null : c)}
      >
        {preview ? (
          <img src={preview} alt="" width={32} height={48} loading="lazy" />
        ) : (
          <span className="patcher-sprite__blank" aria-hidden="true">
            <Icon name={c.random ? 'shuffle' : 'person'} />
          </span>
        )}
        <span className="sprite-choice__text">
          <span className="sprite-choice__name">{label}</span>
          <span className="patcher-hint">{sub}</span>
        </span>
      </button>
    </li>
  )

  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Choose sprite" size="lg">
      <SearchField value={query} onChange={setQuery} label="Search sprites" />
      {sprites === null ? (
        <Spinner label="Loading sprites" />
      ) : (
        <ul className="sprite-grid">
          {!query && choice(LINK, 'Link', 'The default')}
          {!query && choice(RANDOM, 'Random sprite', 'A new one each download')}
          {shown.map((s) =>
            choice({ name: s.name, file: s.file ?? undefined, author: s.author ?? undefined, preview: s.preview ?? undefined }, s.name, s.author ? `By ${s.author}` : '', s.preview),
          )}
        </ul>
      )}
      {sprites !== null && sprites.length === 0 && (
        <EmptyState icon="hide_image" title="Sprites aren't loading">
          alttpr.com didn't answer. Link and Random still work.
        </EmptyState>
      )}
      {sprites !== null && sprites.length > 0 && query && shown.length === 0 && <EmptyState icon="search" title="No sprite by that name" />}
    </Dialog>
  )
}
