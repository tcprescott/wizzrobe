import type { CSSProperties, ReactNode } from 'react'
import { fontStack, type Box } from './data'

interface Props {
  width: number
  height: number
  artUrl?: string | null
  boxes: Box[]
  values: Record<string, string>
  /** Draw holes and empty text boxes with an outline, as the editor does. */
  outlines?: boolean
  /** Wraps each box, for the editor's drag handles. */
  renderBox?: (box: Box, style: CSSProperties, content: ReactNode) => ReactNode
  label: string
  className?: string
}

const pct = (v: number, of: number) => `${(v / of) * 100}%`

/** A layout drawn at any size: its art, then each box at its place with its text. */
export function OverlayView({ width, height, artUrl, boxes, values, outlines, renderBox, label, className }: Props) {
  return (
    <div
      className={['ovl', outlines && 'ovl--outlines', className].filter(Boolean).join(' ')}
      style={{ aspectRatio: `${width} / ${height}` }}
      role={renderBox ? 'group' : label ? 'img' : undefined}
      aria-label={label || undefined}
      aria-hidden={label ? undefined : true}
    >
      {artUrl && <img className="ovl__art" src={artUrl} alt="" draggable={false} />}
      {boxes.map((box) => {
        const style: CSSProperties = {
          left: pct(box.x, width),
          top: pct(box.y, height),
          width: pct(box.w, width),
          height: pct(box.h, height),
        }
        let content: ReactNode = null
        if (box.kind === 'hole') {
          style.borderRadius = `${((box.radius ?? 0) / width) * 100}cqw`
        } else {
          const text = values[box.id] ?? ''
          Object.assign(style, {
            fontFamily: fontStack(box.font),
            fontSize: `${((box.size ?? 24) / width) * 100}cqw`,
            fontWeight: box.weight ?? 800,
            color: box.color ?? '#FFFFFF',
            justifyContent: box.align === 'left' ? 'flex-start' : box.align === 'right' ? 'flex-end' : 'center',
            textAlign: box.align ?? 'center',
            textTransform: box.upper ? 'uppercase' : undefined,
            textShadow: box.shadow ? '0 0.08em 0.2em rgba(0,0,0,.6)' : undefined,
          } satisfies CSSProperties)
          content = <span className="ovl__text">{text}</span>
        }
        const cls = `ovl__box ovl__box--${box.kind}`
        return renderBox ? (
          renderBox(box, style, content)
        ) : (
          <div key={box.id} className={cls} style={style}>
            {content}
          </div>
        )
      })}
    </div>
  )
}
