import { useUiState } from '../utils/uiState'
import { Icon } from './Icon'

const NAV_ITEMS = [
  { id: 'briefing', label: 'Daily Briefing', icon: 'briefing' },
  { id: 'watchlist', label: 'Watchlist', icon: 'star' },
  { id: 'chat', label: 'Chat', icon: 'chat' },
]

export default function Sidebar({ activePage, onNavigate, open = false, userEmail, onSignOut }) {
  const [collapsed, setCollapsed] = useUiState('sidebarCollapsed')
  const cls = `app-sidebar${open ? ' open' : ''}${collapsed ? ' collapsed' : ''}`

  return (
    <nav className={cls}>
      <div className="sidebar-head">
        {!collapsed && <span className="sidebar-brand">QuantAgent</span>}
        <button
          type="button"
          className="sidebar-toggle"
          onClick={() => setCollapsed((c) => !c)}
          title={collapsed ? 'Expand' : 'Collapse'}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          <Icon name={collapsed ? 'chevronsRight' : 'chevronsLeft'} size={14} />
        </button>
      </div>

      <div className="sidebar-nav">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`sidebar-nav-item${activePage === item.id ? ' active' : ''}`}
            onClick={() => onNavigate(item.id)}
            title={collapsed ? item.label : undefined}
          >
            <span className="sidebar-nav-icon"><Icon name={item.icon} size={18} /></span>
            {!collapsed && <span className="sidebar-nav-label">{item.label}</span>}
          </button>
        ))}
      </div>

      {/* Page-specific slot. ChatPage portals session list here. Hidden when sidebar is collapsed. */}
      {!collapsed && <div id="sidebar-extra-slot" className="sidebar-extra" />}

      {userEmail && (
        <div className="sidebar-user">
          {!collapsed && (
            <div className="sidebar-user-email" title={userEmail}>{userEmail}</div>
          )}
          <button
            type="button"
            onClick={onSignOut}
            className="sidebar-signout"
            title="Sign out"
          >
            {collapsed
              ? <Icon name="signOut" size={16} />
              : <>Sign out</>}
          </button>
        </div>
      )}
    </nav>
  )
}
