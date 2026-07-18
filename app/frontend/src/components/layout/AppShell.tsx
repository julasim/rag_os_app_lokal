import { Navigate, Outlet } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import Sidebar from './Sidebar';
import Topbar from './Topbar';

interface AppShellProps {
  title: string;
  sub?: string;
}

export default function AppShell({ title, sub }: AppShellProps) {
  const { isLoggedIn } = useAuth();

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
