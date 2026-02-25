'use client';

import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';

interface User {
  gitlab_user_id: number;
  username: string;
  name: string;
  avatar_url: string;
  is_admin: boolean;
}

interface AuthContextType {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  isAdmin: boolean;
  isLoading: boolean;
  login: () => void;
  logout: () => void;
  setToken: (token: string) => void;
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  token: null,
  isAuthenticated: false,
  isAdmin: false,
  isLoading: true,
  login: () => {},
  logout: () => {},
  setToken: () => {},
});

const JWT_STORAGE_KEY = 'deepwiki_jwt';

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setTokenState] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const logout = useCallback(() => {
    setUser(null);
    setTokenState(null);
    localStorage.removeItem(JWT_STORAGE_KEY);
  }, []);

  const login = useCallback(() => {
    // Redirect via Next.js rewrite proxy so the browser stays on the frontend origin
    window.location.href = `/auth/gitlab/login`;
  }, []);

  // Fetch user info from /auth/me using the stored JWT (proxied via Next.js rewrites)
  const fetchUser = useCallback(async (jwt: string) => {
    try {
      const resp = await fetch(`/auth/me`, {
        headers: { Authorization: `Bearer ${jwt}` },
      });
      if (resp.ok) {
        const data = await resp.json();
        setUser(data);
        return true;
      } else {
        // Token is invalid or expired
        logout();
        return false;
      }
    } catch {
      logout();
      return false;
    }
  }, [logout]);

  const setToken = useCallback((newToken: string) => {
    setTokenState(newToken);
    localStorage.setItem(JWT_STORAGE_KEY, newToken);
    // Immediately fetch user info so isAuthenticated becomes true
    fetchUser(newToken);
  }, [fetchUser]);

  // Initialize from localStorage on mount
  useEffect(() => {
    const stored = localStorage.getItem(JWT_STORAGE_KEY);
    if (stored) {
      setTokenState(stored);
      fetchUser(stored).finally(() => setIsLoading(false));
    } else {
      setIsLoading(false);
    }
  }, [fetchUser]);

  const isAuthenticated = !!user && !!token;
  const isAdmin = !!user?.is_admin;

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        isAuthenticated,
        isAdmin,
        isLoading,
        login,
        logout,
        setToken,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}

/**
 * Helper to get authorization headers for API requests.
 */
export function getAuthHeaders(token: string | null): Record<string, string> {
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}
