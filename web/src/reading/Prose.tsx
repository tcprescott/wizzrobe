/**
 * Draws an article's blocks. The vocabulary is closed, as on the website: each
 * block and span kind has a branch here and anything else draws nothing, so an
 * article's text only ever reaches the page as text.
 */
import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Dialog, Icon, type IconName, type Tone } from '../components'
import { ICONS } from '../components/icons'
import { resolveSitePath, serverUrl, useReadingBase, type Block, type Span } from './data'

const CHIP_TONE: Record<string, Tone> = {
  ok: 'ok', pending: 'pending', live: 'live', neutral: 'neutral', cancelled: 'cancelled', candidate: 'info', new: 'info',
}

const STATE: Record<string, [IconName, Tone]> = {
  Scheduled: ['schedule', 'neutral'],
  'Checked In': ['check', 'neutral'],
  Started: ['play_arrow', 'live'],
  Finished: ['flag', 'pending'],
  Confirmed: ['verified', 'ok'],
}

/** Old Material Icons names the articles use, under their Material Symbols names. */
const RENAMED: Record<string, IconName> = { emoji_events: 'trophy', new_releases: 'release_alert' }

/** An icon an article names, or a plain one when the app doesn't ship it. */
export function contentIcon(name: string): IconName {
  const renamed = RENAMED[name]
  if (renamed) return renamed
  return name in ICONS ? (name as IconName) : 'description'
}

function SpanLink({ span }: { span: Span }) {
  const { base, community } = useReadingBase()
  if (span.href.startsWith('#')) return <a href={span.href} className="prose-link">{span.text}</a>
  if (span.href.startsWith('/')) {
    const target = resolveSitePath(span.href, base, community)
    if ('to' in target) return <Link to={target.to} className="prose-link">{span.text}</Link>
    return <a href={target.href} className="prose-link">{span.text}</a>
  }
  return (
    <a href={span.href} className="prose-link" target="_blank" rel="noopener noreferrer">
      {span.text}
      <span className="visually-hidden"> (opens in a new tab)</span>
    </a>
  )
}

function SpanView({ span }: { span: Span }) {
  switch (span.kind) {
    case 'link':
      return <SpanLink span={span} />
    case 'strong':
      return <strong>{span.text}</strong>
    case 'em':
      return <em>{span.text}</em>
    case 'code':
      return <code className="prose-code">{span.text}</code>
    case 'chip':
      return <span className={`chip chip--${CHIP_TONE[span.tone] ?? 'neutral'}`}>{span.text}</span>
    case 'state': {
      const [icon, tone] = STATE[span.text] ?? ['schedule', 'neutral']
      return (
        <span className={`prose-state prose-state--${tone}`}>
          <Icon name={icon} size={16} />
          {span.text}
        </span>
      )
    }
    default:
      return <>{span.text}</>
  }
}

export function Spans({ spans }: { spans: Span[] }) {
  return (
    <>
      {spans.map((span, i) => (
        <SpanView key={i} span={span} />
      ))}
    </>
  )
}

function Figure({ block }: { block: Block }) {
  const [open, setOpen] = useState(false)
  const src = serverUrl(block.src)
  return (
    <figure className="prose-figure">
      <button type="button" className="prose-figure__open" onClick={() => setOpen(true)} aria-label={`Enlarge: ${block.alt}`}>
        <img src={src} alt={block.alt} loading="lazy" decoding="async" />
      </button>
      {block.spans.length > 0 && (
        <figcaption>
          <Spans spans={block.spans} />
        </figcaption>
      )}
      <Dialog open={open} onOpenChange={setOpen} title={block.alt || 'Screenshot'} size="lg">
        <img className="prose-zoom" src={src} alt={block.alt} />
      </Dialog>
    </figure>
  )
}

function Heading({ block }: { block: Block }) {
  const level = Math.min(Math.max(block.level, 2), 4)
  const content = <Spans spans={block.spans} />
  const id = block.anchor || undefined
  if (level === 2) return <h2 id={id} className="prose-h2">{content}</h2>
  if (level === 3) return <h3 id={id} className="prose-h3">{content}</h3>
  return <h4 id={id} className="prose-h4">{content}</h4>
}

/** `embeds` maps an embed's name to what draws it; an embed with none draws nothing. */
export function Prose({ blocks, embeds = {} }: { blocks: Block[]; embeds?: Record<string, ReactNode> }) {
  return (
    <div className="prose">
      {blocks.map((block, i) => {
        switch (block.kind) {
          case 'heading':
            return <Heading key={i} block={block} />
          case 'para':
            return (
              <p key={i}>
                <Spans spans={block.spans} />
              </p>
            )
          case 'bullets':
          case 'numbers': {
            const items = block.items.map((item, j) => (
              <li key={j}>
                <Spans spans={item} />
              </li>
            ))
            return block.kind === 'bullets' ? <ul key={i}>{items}</ul> : <ol key={i}>{items}</ol>
          }
          case 'table':
            return (
              <div key={i} className="prose-table" tabIndex={0} role="region" aria-label="Table">
                <table>
                  {block.headers.length > 0 && (
                    <thead>
                      <tr>
                        {block.headers.map((cell, j) => (
                          <th key={j} scope="col">
                            <Spans spans={cell} />
                          </th>
                        ))}
                      </tr>
                    </thead>
                  )}
                  <tbody>
                    {block.rows.map((row, j) => (
                      <tr key={j}>
                        {row.map((cell, k) => (
                          <td key={k}>
                            <Spans spans={cell} />
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          case 'note':
            return (
              <aside key={i} className="prose-note">
                <Icon name="info" size={20} />
                <span>
                  <Spans spans={block.spans} />
                </span>
              </aside>
            )
          case 'image':
            return <Figure key={i} block={block} />
          case 'embed':
            return <div key={i}>{embeds[block.name] ?? null}</div>
          default:
            return null
        }
      })}
    </div>
  )
}
