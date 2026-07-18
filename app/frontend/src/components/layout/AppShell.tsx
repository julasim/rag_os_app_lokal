import { Navigate, Outlet } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import Sidebar from './Sidebar';
import Topbar from './Topbar';

interface AppShellProps {
  title: string;
  sub?: string;
}

export default function AppShell({ title, sub }: AppShellProps) {
  const { isLoggedIn, ready } = useAuth();

  // Warten, bis der lokale Auto-Login-Check (/api/auth/me) durch ist — sonst
  // blitzt die Login-Seite kurz auf, bevor die lokale Session steht.
  if (!ready) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', fontSize: 13, color: '#a3a3a3', background: '#fafafa' }}>
        Lädt…
      </div>
    );
  }

  if (!isLoggedIn) {
    return <Navigate to="/login" replace />;
  }

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      <Sidebar />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <Topbar title={title} sub={sub} />
        <main
          style={{
            flex: 1,
            overflowY: 'auto',
            background: '#fafafa',
            padding: 24,
          }}
        >
          <Outlet />
        </main>
      </div>
    </div>
  );
}
