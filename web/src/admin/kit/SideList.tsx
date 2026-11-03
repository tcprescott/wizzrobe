import type { ReactNode } from 'react'
import { Link, NavLink } from 'react-router-dom'
import { Icon, type IconName } from '../../components'
import { usePhone } from '../../lib/usePhone'
import './admin.css'
import './sidelist.css'

interface SideListLayoutProps {
  /** The list (a `SideListCard` of `SideListRow`s), with anything that sits above it (pills, search, a panel). */
  list: ReactNode
  /** The open item, or a prompt to pick one. */
  detail: ReactNode
  /** Whether the URL names an item: on a phone that shows the item instead of the list. */
  open: boolean
  /** Where the phone's back link goes, and what it says ("Members"). */
  backTo: string
  backLabel: string
}

/**
 * A list beside the open item on desktop. On a phone the list is its own page
 * and each item opens on its own page with a back link, like the Me screen.
 */
export function SideListLayout({ list, detail, open, backTo, backLabel }: SideListLayoutProps) {
  const phone = usePhone()
  if (phone) {
    return open ? (
      <div className="side-list side-list--phone">
        <Link to={backTo} className="admin-back">
          <Icon name="chevron_left" />
          {backLabel}
        </Link>
        {detail}
      </div>
    ) : (
      <div className="side-list side-list--phone">{list}</div>
    )
  }
  return (
    <div className="side-list">
      <div>{list}</div>
      <div>{detail}</div>
    </div>
  )
}

/** The white card the rows sit in; `label` names the list for a screen reader. */
export function SideListCard({
  label,
  title,
  children,
  more,
}: {
  label: string
  /** A small-caps heading inside the card, for a list split into groups (Stages, Layouts). */
  title?: ReactNode
  children: ReactNode
  more?: ReactNode
}) {
  return (
    <div className="side-list__list">
      {title && <h2 className="side-list__heading">{title}</h2>}
      <ul className="side-list__rows" aria-label={label}>
        {children}
      </ul>
      {more && <p className="side-list__more">{more}</p>}
    </div>
  )
}

/** One item: opens its own URL, highlighted while open. */
export function SideListRow({
  to,
  title,
  sub,
  leading,
  end,
}: {
  to: string
  title: ReactNode
  sub?: ReactNode
  leading?: ReactNode
  end?: ReactNode
}) {
  const phone = usePhone()
  return (
    <li>
      <NavLink to={to} className="side-list__row">
        {leading}
        <span className="side-list__text">
          <span className="side-list__title">{title}</span>
          {sub && <span className="side-list__sub">{sub}</span>}
        </span>
        {end}
        {phone && <Icon name="chevron_right" className="side-list__chev" />}
      </NavLink>
    </li>
  )
}

/** The open item: its header (name, a line under it, its buttons), sections, and an optional footer. */
export function Detail({
  title,
  sub,
  leading,
  actions,
  footer,
  children,
}: {
  title: ReactNode
  sub?: ReactNode
  leading?: ReactNode
  actions?: ReactNode
  footer?: ReactNode
  children: ReactNode
}) {
  return (
    <article className="detail">
      <header className="detail__head">
        {leading}
        <div className="detail__titles">
          <h2 className="detail__title">{title}</h2>
          {sub && <span className="detail__sub">{sub}</span>}
        </div>
        {actions && <div className="detail__actions">{actions}</div>}
      </header>
      {children}
      {footer && <footer className="detail__foot">{footer}</footer>}
    </article>
  )
}

/** What the desktop detail pane shows before anything is picked. */
export function DetailEmpty({ children }: { children: ReactNode }) {
  return <div className="detail-empty">{children}</div>
}

/** One titled block inside an item. */
export function DetailSection({ title, children }: { title?: ReactNode; children: ReactNode }) {
  return (
    <section className="detail-section">
      {title && <h3 className="detail-section__title">{title}</h3>}
      {children}
    </section>
  )
}

/** An icon, a line of text with an optional second line, and something on the right. */
export function DetailLine({
  icon,
  children,
  sub,
  end,
}: {
  icon: IconName
  children: ReactNode
  sub?: ReactNode
  end?: ReactNode
}) {
  return (
    <div className="detail-line">
      <Icon name={icon} size={20} />
      <div className="detail-line__text">
        <span>{children}</span>
        {sub && <span className="detail-line__sub">{sub}</span>}
      </div>
      {end}
    </div>
  )
}

interface SideListProps<T> {
  /** Names the list for screen readers, e.g. "Tournaments". */
  label: string
  items: T[]
  itemKey: (item: T) => string | number
  /** Where tapping an item goes (absolute path). */
  itemTo: (item: T) => string
  title: (item: T) => ReactNode
  /** Chips and a status line under the title. */
  sub?: (item: T) => ReactNode
  /** The open item's key (from the screen's own nested `:id` route), or null when none is open. */
  openKey: string | number | null
  /** The open item, drawn beside the list on desktop and alone on a phone. */
  detail: ReactNode
  /** Desktop, nothing open yet. */
  placeholder?: ReactNode
  /** Phone, an item open: the link back to the list. */
  back: { to: string; label: string }
  /** Filters or a search box above the list. */
  toolbar?: ReactNode
  empty?: ReactNode
}

/**
 * The side list from data, for the common case. A screen that needs more in
 * the list column (a panel above the rows, rows with avatars) builds the same
 * thing from `SideListLayout`, `SideListCard` and `SideListRow`.
 */
export function SideList<T>({
  label,
  items,
  itemKey,
  itemTo,
  title,
  sub,
  openKey,
  detail,
  placeholder,
  back,
  toolbar,
  empty,
}: SideListProps<T>) {
  const list = (
    <div className="side-list__col">
      {toolbar}
      {items.length === 0 ? (
        empty
      ) : (
        <SideListCard label={label}>
          {items.map((item) => (
            <SideListRow key={itemKey(item)} to={itemTo(item)} title={title(item)} sub={sub?.(item)} />
          ))}
        </SideListCard>
      )}
    </div>
  )
  return (
    <SideListLayout
      list={list}
      detail={openKey != null ? detail : (placeholder ?? null)}
      open={openKey != null}
      backTo={back.to}
      backLabel={back.label}
    />
  )
}
