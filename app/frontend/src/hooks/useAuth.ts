import React, { createContext, useContext, useEffect, useState } from 'react';
import { setToken, clearToken, apiGet } from '../api/client';
import { login as apiLogin, logout as apiLogout } from '../api/auth';
import type { User } from '../types';

interface AuthCtx {
  user: User | null;
  ready: boolean;
  isLoggedIn: boolean;
  isAdmin: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(() => {
    const raw = localStorage.getItem('rag_user');
    return raw ? (JSON.parse(raw) as User) : null;
  });
  const [ready, setReady] = useState<boolean>(() => !!localStorage.getItem('rag_user'));

  // Lokaler Desktop-Modus: ohne gespeicherten User einmal /me versuchen. Das
  // Backend liefert im lokalen Modus (127.0.0.1, Ein-Nutzer) automatisch den
  // Admin → kein Login-Wall. Schlägt es fehl (echter Mehrbenutzer-Betrieb),
  // bleibt user=null und die Login-Seite greift.
  useEffect(() => {
    if (user) {
      setReady(true);
      return;
    }
    let cancelled = false;
    apiGet<User>('/api/auth/me')
      .then((u) => {
        if (!cancelled) {
          localStorage.setItem('rag_user', JSON.stringify(u));
          setUser(u);
        }
      })
      .catch(() => {
        /* nicht lokal / nicht eingeloggt → Login-Seite */
      })
      .finally(() => {
        if (!cancelled) setReady(true);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const isLoggedIn = !!user;
  const isAdmin = user?.role === 'admin';

  async function login(email: string, password: string) {
    const res = await apiLogin(email, password);
    setToken(res.token);
    localStorage.setItem('rag_user', JSON.stringify(res.user));
    setUser(res.user);
  }

  async function logout() {
    await apiLogout();
    clearToken();
    localStorage.removeItem('rag_user');
    setUser(null);
  }

  return React.createElement(
    AuthContext.Provider,
    { value: { user, ready, isLoggedIn, isAdmin, login, logout } },
    children
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be inside AuthProvider');
  return ctx;
}
