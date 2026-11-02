import { NavLink } from 'react-router-dom'
import { CountBadge, Icon, type IconName } from '../../components'
import './admin.css'

export interface SectionItem {
  key: string
  label: string
  icon: IconName
  to: string
  /** One-line status under the label, shown in the full (phone) list only. */
  sub?: string
  count?: number
}

export interface SectionGroup {
  title: string
  items: SectionItem[]
}

/**
 * A grouped list of sections. `compact` is the desktop column beside the open
 * section (labels only); the full list is the phone screen you tap into.
 * Admin and Platform both draw their menus with it.
 */
export function SectionNav({ groups, label, compact = false }: { groups: SectionGroup[]; label: string; compact?: boolean }) {
  return (
    <nav className={`section-nav${compact ? ' section-nav--compact' : ''}`} aria-label={label}>
      {groups.map((group) => {
        const id = `sn-${label}-${group.title}`.toLowerCase().replace(/[^a-z0-9]+/g, '-')
        return (
          <div key={group.title} role="group" aria-labelledby={id}>
            <h2 className="section-nav__group" id={id}>
              {group.title}
            </h2>
            <ul className="section-nav__list">
              {group.items.map((item) => (
                <li key={item.key}>
                  <NavLink to={item.to} className="section-nav__row">
                    <Icon name={item.icon} />
                    <span className="section-nav__text">
                      <span className="section-nav__title">{item.label}</span>
                      {!compact && item.sub && <span className="section-nav__sub">{item.sub}</span>}
                    </span>
                    <CountBadge count={item.count ?? 0} label={`${item.count ?? 0} need you`} />
                    {!compact && <Icon name="chevron_right" />}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        )
      })}
    </nav>
  )
}
