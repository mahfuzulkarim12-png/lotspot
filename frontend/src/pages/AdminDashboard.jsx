import { Link, NavLink, Outlet } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useInventory } from '../hooks/useInventory';

const TABS = [
  { to: 'pos', label: 'POS' },
  { to: 'inventory', label: 'Inventory' },
  { to: 'summary', label: 'Daily summary' },
  { to: 'history', label: 'History' },
  { to: 'timeclock', label: 'Time clock' },
  { to: 'timeclock-history', label: 'Shift history' },
  { to: 'tax-settings', label: 'Tax settings' },
];

export default function AdminDashboard() {
  const { user, logout } = useAuth();
  // One SSE connection for the whole dashboard; panels get it via Outlet context.
  const inventory = useInventory();

  return (
    <>
      <header className="topbar">
        <Link to="/" className="brand">
          Lot<em>Spot</em>
        </Link>
        <span className="topbar-note">Back office</span>
        <span className="topbar-spacer" />
        <span className={`live-dot${inventory.connected ? ' is-live' : ''}`}>
          {inventory.connected ? 'Live' : 'Reconnecting'}
        </span>
        <span className="topbar-note">{user}</span>
        <button className="btn btn-ghost" type="button" onClick={logout}>
          Sign out
        </button>
      </header>

      <main className="admin-layout">
        <nav className="tab-rail" aria-label="Admin sections">
          {TABS.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              className={({ isActive }) => `tab-link${isActive ? ' active' : ''}`}
            >
              {tab.label}
            </NavLink>
          ))}
        </nav>

        <Outlet context={inventory} />
      </main>
    </>
  );
}
