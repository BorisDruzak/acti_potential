import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, apiRequest } from "../api/client";
import { useAuth } from "../auth/AuthProvider";

type OidcConfig = { enabled: boolean; label: string };
const oidcFailure =
  "Не удалось выполнить единый вход. Повторите попытку или используйте локальную учётную запись. Если ошибка повторяется, обратитесь к администратору.";

export function Login() {
  const auth = useAuth();
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [oidcBusy, setOidcBusy] = useState(false);
  const [oidc, setOidc] = useState<OidcConfig | null>(null);
  const [error, setError] = useState(() =>
    new URL(window.location.href).searchParams.has("auth_error")
      ? oidcFailure
      : "",
  );
  const submitting = useRef(false);
  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.has("auth_error")) {
      url.searchParams.delete("auth_error");
      window.history.replaceState(
        window.history.state,
        "",
        url.pathname + url.search + url.hash,
      );
    }
    const controller = new AbortController();
    void apiRequest<OidcConfig>("/auth/oidc/config", { signal: controller.signal })
      .then((config) => {
        if (!controller.signal.aborted && config?.enabled === true)
          setOidc(config);
      })
      .catch(() => {});
    return () => controller.abort();
  }, []);

  async function startOidc() {
    if (submitting.current) return;
    submitting.current = true;
    setOidcBusy(true);
    setError("");
    try {
      const result = await apiRequest<{ authorization_url: string }>(
        "/auth/oidc/start",
        { method: "POST" },
      );
      const target = new URL(result.authorization_url);
      if (target.protocol !== "https:" || target.username || target.password)
        throw new Error("Invalid identity provider URL");
      window.location.assign(target.href);
    } catch {
      setError(oidcFailure);
      submitting.current = false;
      setOidcBusy(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      await auth.login(login, password);
    } catch (failure) {
      setError(
        failure instanceof ApiError
          ? failure.message +
              (failure.requestId ? ` Код обращения: ${failure.requestId}` : "")
          : "Не удалось выполнить вход",
      );
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  return (
    <main className="login-page">
      <section className="login-intro">
        <a className="brand" href="/" aria-label="ACTI, главная">
          ACTI<span>v3</span>
        </a>
        <div>
          <span className="eyebrow">ОРГАНИЗАЦИОННАЯ ПАМЯТЬ</span>
          <h1>
            Знания и процессы.
            <br />В одном пространстве.
          </h1>
          <p>
            Основа для работы с документами,
            <br />
            основаниями и опытом администрации.
          </p>
        </div>
        <span className="intro-footer">
          Единая система · Локальная инфраструктура
        </span>
      </section>
      <section className="login-content">
        <div className="login-card">
          <span className="eyebrow">ДОБРО ПОЖАЛОВАТЬ</span>
          <h2>Вход в ACTI</h2>
          {oidc && (
            <div className="oidc-login">
              <button
                className="primary"
                type="button"
                disabled={busy || oidcBusy}
                onClick={() => void startOidc()}
              >
                {oidcBusy
                  ? "Переход к единому входу…"
                  : `Войти через ${oidc.label}`}
              </button>
              <p className="muted">Или используйте локальную учётную запись.</p>
            </div>
          )}
          {!oidc && (
            <p className="muted">Используйте локальную учётную запись.</p>
          )}
          <form onSubmit={submit} aria-label="Вход в ACTI">
            <label htmlFor="login">Логин</label>
            <input
              id="login"
              name="username"
              autoComplete="username"
              required
              maxLength={128}
              value={login}
              onChange={(e) => setLogin(e.target.value)}
              autoFocus
            />
            <label htmlFor="password">Пароль</label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              maxLength={1024}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {error && (
              <div className="error" role="alert">
                {error}
              </div>
            )}
            <button className="primary" type="submit" disabled={busy || oidcBusy}>
              {busy ? "Выполняется вход…" : "Войти в рабочее пространство"}
              <span aria-hidden="true">→</span>
            </button>
          </form>
          <p className="login-help">
            Если у вас нет учётной записи, обратитесь к администратору системы.
          </p>
        </div>
        <span className="login-caption">ACTI · Платформенное ядро</span>
      </section>
    </main>
  );
}
