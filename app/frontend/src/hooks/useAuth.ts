import React, { createContext, useContext, useState } from 'react';
import { getToken, setToken, clearToken } from '../api/client';
import { login as apiLogin, logout as apiLogout } from '../api/auth';
import type { User } from '../types';

interface AuthCtx {
  user: User | null;
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

  const isLoggedIn = !!user && !!getToken();
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
    { value: { user, isLoggedIn, isAdmin, login, logout } },
    children
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be inside AuthProvider');
  return ctx;
}
