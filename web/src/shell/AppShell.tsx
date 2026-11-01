import { Outlet, useLocation } from 'react-router-dom'
import { useCommunity } from '../community/CommunityProvider'
import { CommunityPicker } from './CommunityPicker'
import { NavTabs } from './NavTabs'
import { TABS } from './tabs'
import './shell.css'

function currentTitle(pathname: string): string {
  const key = pathname.split('/').filter(Boolean).at(-1)
  return TABS.find((tab) => tab.key === key)?.label ?? 'Wizzrobe'
}

export function AppShell() {
  const { community } = useCommunity()
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
