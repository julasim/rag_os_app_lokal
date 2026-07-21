import React, { Suspense } from 'react';
import { createBrowserRouter, RouterProvider, Navigate } from 'react-router-dom';
import AppShell from './components/layout/AppShell';

const Login = React.lazy(() => import('./pages/Login'));
const Dashboard = React.lazy(() => import('./pages/Dashboard'));
const Documents = React.lazy(() => import('./pages/Documents'));
const Keys = React.lazy(() => import('./pages/Keys'));
const Users = React.lazy(() => import('./pages/Users'));
const System = React.lazy(() => import('./pages/System'));
const Maintenance = React.lazy(() => import('./pages/Maintenance'));
const Graph = React.lazy(() => import('./pages/Graph'));

function Loading() {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100vh',
        fontSize: 13,
        color: '#a3a3a3',
        background: '#fafafa',
      }}
    >
      Lädt…
    </div>
  );
}

const router = createBrowserRouter([
  {
    path: '/login',
    element: (
      <Suspense fallback={<Loading />}>
        <Login />
      </Suspense>
    ),
  },
  {
    path: '/',
    element: <Navigate to="/dashboard" replace />,
  },
  {
    path: '/dashboard',
    element: (
      <AppShell
        title="Dashboard"
        sub="Health-Status und Übersicht"
      />
    ),
    children: [
      {
        index: true,
        element: (
          <Suspense fallback={<Loading />}>
            <Dashboard />
          </Suspense>
        ),
      },
    ],
  },
  {
    path: '/documents',
    element: <AppShell title="Dokumente" />,
    children: [
      {
        index: true,
        element: (
          <Suspense fallback={<Loading />}>
            <Documents />
          </Suspense>
        ),
      },
    ],
  },
  {
    path: '/keys',
    element: (
      <AppShell
        title="API-Keys"
        sub="Zugangstoken für Programme"
      />
    ),
    children: [
      {
        index: true,
        element: (
          <Suspense fallback={<Loading />}>
            <Keys />
          </Suspense>
        ),
      },
    ],
  },
  {
    path: '/users',
    element: (
      <AppShell
        title="Nutzer"
        sub="Konten, Rollen und Ordner-Zugriff"
      />
    ),
    children: [
      {
        index: true,
        element: (
          <Suspense fallback={<Loading />}>
            <Users />
          </Suspense>
        ),
      },
    ],
  },
  {
    path: '/system',
    element: <AppShell title="System" />,
    children: [
      {
        index: true,
        element: (
          <Suspense fallback={<Loading />}>
            <System />
          </Suspense>
        ),
      },
    ],
  },
  {
    path: '/maintenance',
    element: (
      <AppShell
        title="Wartung"
        sub="Duplikate, Tag-Synonyme, Wartungsläufe"
      />
    ),
    children: [
      {
        index: true,
        element: (
          <Suspense fallback={<Loading />}>
            <Maintenance />
          </Suspense>
        ),
      },
    ],
  },
  {
    path: '/graph',
    element: (
      <AppShell
        title="Wissensgraph"
        sub="Dokumente ↔ Normen/Tags — interaktiv"
      />
    ),
    children: [
      {
        index: true,
        element: (
          <Suspense fallback={<Loading />}>
            <Graph />
          </Suspense>
        ),
      },
    ],
  },
  {
    path: '*',
    element: <Navigate to="/dashboard" replace />,
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
