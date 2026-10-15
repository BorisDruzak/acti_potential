import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { ApiError, apiRequest } from "../api/client";

export type CurrentUser = {
  id: string;
  display_name: string;
  roles: string[];
  csrf_token: string;
};
type Status = "loading" | "anonymous" | "authenticated" | "unavailable";
type Auth = {
  status: Status;
  user: CurrentUser | null;
  error: string;
  refresh: () => Promise<void>;
  login: (login: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
};
const Context = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [error, setError] = useState("");
  const refresh = useCallback(async (signal?: AbortSignal) => {
    setStatus("loading");
    setError("");
    try {
      const current = await apiRequest<CurrentUser>("/users/me", { signal });
      if (!signal?.aborted) {
        setUser(current);
        setStatus("authenticated");
      }
    } catch (failure) {
      if (signal?.aborted) return;
      if (failure instanceof ApiError && failure.status === 401) {
        setUser(null);
        setStatus("anonymous");
      } else {
        setError(
          failure instanceof Error
            ? failure.message
            : "Сервис временно недоступен",
        );
        setStatus("unavailable");
      }
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const expired = () => {
      setUser(null);
      setStatus("anonymous");
    };
    window.addEventListener("acti:unauthorized", expired);
    void refresh(controller.signal);
    return () => {
      controller.abort();
      window.removeEventListener("acti:unauthorized", expired);
    };
  }, [refresh]);
  async function login(login: string, password: string) {
    await apiRequest("/auth/login", {
      method: "POST",
      body: JSON.stringify({ login, password }),
    });
    await refresh();
  }
  async function logout() {
    await apiRequest("/auth/logout", {
      method: "POST",
      headers: { "X-CSRF-Token": user?.csrf_token ?? "" },
    });
    setUser(null);
    setStatus("anonymous");
  }
  return (
    <Context.Provider value={{ status, user, error, refresh, login, logout }}>
      {children}
    </Context.Provider>
  );
}

export function useAuth() {
  const auth = useContext(Context);
  if (!auth) throw new Error("AuthProvider is required");
  return auth;
}
