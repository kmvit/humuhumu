import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { ApiError, clearTokens, get, post, setTokens, getToken } from "./api";
import type { Me } from "./types";

interface AuthState {
  user: Me | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (name: string, phone: string) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  async function loadMe() {
    if (!getToken()) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      setUser(await get<Me>("/users/me/"));
    } catch (e) {
      // токены сбрасываем только если refresh не помог (реальный 401);
      // при сетевом сбое оставляем сессию — попробуем позже
      if (e instanceof ApiError && e.status === 401) clearTokens();
      setUser(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadMe();
  }, []);

  async function login(username: string, password: string) {
    const tokens = await post<{ access: string; refresh: string }>(
      "/auth/token/",
      { username, password }
    );
    setTokens(tokens.access, tokens.refresh);
    await loadMe();
  }

  // Регистрация только собирает данные гостя — логин и пароль высылаем отдельно.
  async function register(name: string, phone: string) {
    await post("/auth/register/", { name, phone });
  }

  function logout() {
    clearTokens();
    setUser(null);
  }

  return (
    <AuthContext.Provider
      value={{ user, loading, login, register, logout, refresh: loadMe }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth вне AuthProvider");
  return ctx;
}
