import { Link, Outlet, useLocation } from 'react-router-dom'
import { Icon, Tooltip } from '../components'
import { useCommunity } from '../community/CommunityProvider'
import { CommunityPicker } from './CommunityPicker'
import { NavTabs } from './NavTabs'
import { TABS } from './tabs'
import './shell.css'

const READING_TITLES: Record<string, string> = {
  help: 'Help', 'event-info': 'Event information', changelog: "What's new", about: 'About', facts: 'Facts',
}

function currentTitle(pathname: string): string {
  const parts = pathname.split('/').filter(Boolean)
  const reading = parts.find((part) => part in READING_TITLES)
  if (reading) return READING_TITLES[reading] ?? 'Wizzrobe'
  return TABS.find((tab) => tab.key === parts.at(-1))?.label ?? 'Wizzrobe'
}

export function AppShell() {
  const { community, base } = useCommunity()
  const { pathname } = useLocation()
  const title = currentTitle(pathname)
  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <nav className="shell__rail" aria-label="Main">
        <div className="brand">
          <span className="brand__mark" aria-hidden="true" />
          <span className="brand__name">Wizzrobe</span>
        </div>
        <NavTabs variant="rail" />
      </nav>
      <div className="shell__main">
        <header className="topbar">
          <span className="topbar__title">{title}</span>
          <span className="topbar__community">{community.name}</span>
          <CommunityPicker />
          <Tooltip content="Help">
            <Link to={`${base}/help`} className="icon-btn topbar__help" aria-label="Help">
              <Icon name="help" />
            </Link>
          </Tooltip>
        </header>
        <main id="main" className="shell__content" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
      <nav className="shell__tabbar" aria-label="Main">
        <NavTabs variant="tabbar" />
      </nav>
    </div>
  )
}
