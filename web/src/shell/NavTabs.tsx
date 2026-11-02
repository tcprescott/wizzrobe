import { NavLink, useLocation } from 'react-router-dom'
import { CountBadge, Icon } from '../components'
import { useCommunity } from '../community/CommunityProvider'
import { visibleTabs } from './tabs'

/**
 * One list of destinations, drawn as a left rail on desktop and a bottom tab
 * bar on phone. Both are in the DOM; CSS shows one, and the hidden one is
 * display:none so it's out of the accessibility tree too.
 */
export function NavTabs({ variant, counts = {} }: { variant: 'rail' | 'tabbar'; counts?: Partial<Record<string, number>> }) {
  const { base, hasWork } = useCommunity()
  const { pathname } = useLocation()
  // Admin keeps its own /admin URLs (links in old DMs use them) but lives under Work.
  const inAdmin = pathname === `${base}/admin` || pathname.startsWith(`${base}/admin/`)
  return (
    <ul className={`nav nav--${variant}`}>
      {visibleTabs(hasWork).map((tab) => {
        const count = counts[tab.key] ?? 0
        return (
          <li key={tab.key}>
            <NavLink
              to={`${base}/${tab.key}`}
              className={({ isActive }) => (isActive || (tab.key === 'work' && inAdmin) ? 'nav__link active' : 'nav__link')}
            >
              {({ isActive: onTab }) => {
                const isActive = onTab || (tab.key === 'work' && inAdmin)
                return (
                  <>
                    <span className="nav__icon">
                      <Icon name={tab.icon} filled={isActive} />
                    </span>
                    <span className="nav__label">{tab.label}</span>
                    <CountBadge count={count} label={`${count} need you`} />
                  </>
                )
              }}
            </NavLink>
          </li>
        )
      })}
    </ul>
  )
}
