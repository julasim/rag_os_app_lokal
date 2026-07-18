import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';

interface NavItem {
  path: string;
  label: string;
}

const workspaceNav: NavItem[] = [
  { path: '/dashboard', label: 'Dashboard' },
  { path: '/documents', label: 'Dokumente' },
];

const adminNav: NavItem[] = [
  { path: '/users', label: 'Nutzer' },
  { path: '/keys', label: 'API-Keys' },
  { path: '/system', label: 'System' },
  { path: '/maintenance', label: 'Wartung' },
];

export default function Sidebar() {
  const location = useLocation();
  const { user, logout } = useAuth();

  const isActive = (path: string) =>
    location.pathname === path || location.pathname.startsWith(path + '/');

  const navItemClass = (path: string) =>
    [
      'flex items-center gap-2 px-3 py-1.5 rounded-md text-[13px] transition-colors cursor-pointer no-underline',
      isActive(path)
        ? 'bg-[#f5f5f5] text-[#111] font-medium'
        : 'text-[#525252] hover:bg-[#f5f5f5]',
    ].join(' ');

  const initials = user?.email
    ? user.email.slice(0, 2).toUpperCase()
    : 'JS';

  return (
    <aside
      style={{
        width: 220,
        minWidth: 220,
        background: '#ffffff',
        borderRight: '1px solid #ededed',
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        padding: '0',
      }}
    >
      {/* Brand */}
      <div style={{ padding: '18px 16px 14px', borderBottom: '1px solid #ededed' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div
            style={{
              width: 26,
              height: 26,
              background: '#111111',
              borderRadius: 6,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
            }}
          >
            <span style={{ color: '#fff', fontWeight: 700, fontSize: 13, lineHeight: 1 }}>R</span>
          </div>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#111', lineHeight: 1.2 }}>
              RAG OS
            </div>
            <div style={{ fontSize: 11, color: '#a3a3a3', lineHeight: 1.2 }}>sima.business</div>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav style={{ flex: 1, padding: '12px 8px', overflowY: 'auto' }}>
        {/* Arbeitsbereich */}
        <div style={{ marginBottom: 16 }}>
          <div
            style={{
              fontSize: 10,
              fontWeight: 600,
              color: '#a3a3a3',
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              padding: '0 8px',
              marginBottom: 4,
            }}
          >
            Arbeitsbereich
          </div>
          {workspaceNav.map((item) => (
            <Link key={item.path} to={item.path} className={navItemClass(item.path)}>
              <span
                style={{
                  width: 4,
                  height: 4,
                  borderRadius: '50%',
                  background: '#d4d4d4',
                  flexShrink: 0,
                }}
              />
              {item.label}
            </Link>
          ))}
        </div>

        {/* Verwaltung */}
        <div>
          <div
            style={{
              fontSize: 10,
              fontWeight: 600,
              color: '#a3a3a3',
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              padding: '0 8px',
              marginBottom: 4,
            }}
          >
            Verwaltung
          </div>
          {adminNav.map((item) => (
            <Link key={item.path} to={item.path} className={navItemClass(item.path)}>
              <span
                style={{
                  width: 4,
                  height: 4,
                  borderRadius: '50%',
                  background: '#d4d4d4',
                  flexShrink: 0,
                }}
              />
              {item.label}
            </Link>
          ))}
        </div>
      </nav>

      {/* User Card */}
      <div
        style={{
          padding: '12px 16px',
          borderTop: '1px solid #ededed',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}
      >
        <div
          style={{
            width: 26,
            height: 26,
            borderRadius: '50%',
            background: '#f5f5f5',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 10,
            fontWeight: 600,
            color: '#525252',
            flexShrink: 0,
          }}
        >
          {initials}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 12,
              fontWeight: 500,
              color: '#111',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {user?.email ?? '—'}
          </div>
          <div style={{ fontSize: 10, color: '#a3a3a3' }}>
            {user?.role === 'admin' ? 'Admin' : 'Nutzer'}
          </div>
        </div>
        <button
          onClick={() => logout()}
          title="Abmelden"
          style={{
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            padding: 4,
            color: '#a3a3a3',
            fontSize: 14,
            lineHeight: 1,
            borderRadius: 4,
          }}
          className="hover:text-[#111] transition-colors"
        >
          ⏻
        </button>
      </div>
    </aside>
  );
}
