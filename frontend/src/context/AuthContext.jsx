import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { api, clearToken, getToken, setToken } from '../api/client';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  // 'checking' until we know whether a stored token is still valid
  const [status, setStatus] = useState(getToken() ? 'checking' : 'anonymous');

  useEffect(() => {
    if (status !== 'checking') return;
    api
      .me()
      .then((me) => {
        setUser(me.username);
        setStatus('authenticated');
      })
      .catch(() => {
        clearToken();
        setStatus('anonymous');
      });
  }, [status]);

  const login = useCallback(async (username, password) => {
    const session = await api.login(username, password);
    setToken(session.token);
    setUser(session.username);
    setStatus('authenticated');
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // token may already be expired server-side; local logout still proceeds
    }
    clearToken();
    setUser(null);
    setStatus('anonymous');
  }, []);

  return (
    <AuthContext.Provider value={{ user, status, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
